"""TL routing supplies only classification and the developer who will do the work."""

from django.db import transaction
from django.utils import timezone

from apps.accounts.models import UserRole
from apps.audit.models import AuditAction
from apps.tickets.constants import ClassificationStatus, TicketStatus, TicketType
from apps.tickets.models import SupportTicket, TicketAssignmentHistory
from apps.tickets.services.activity_service import record_activity
from apps.tickets.services.ticket_number import generate_ticket_no
from common.exceptions.domain import WorkflowValidationError
from common.services.audit import record_audit
from common.utils.dates import local_today
from common.permissions.company import require_company_access, validate_company_relations, is_super_admin


@transaction.atomic
def route_ticket(*, ticket, actor, ticket_type, owner, request=None):
    locked = SupportTicket.objects.select_for_update().get(pk=ticket.pk)
    require_company_access(actor, locked)
    if locked.company_id is None and is_super_admin(actor):
        locked.company = owner.company
        if locked.bug_id:
            locked.bug.company = owner.company
            locked.bug.save(update_fields=["company", "updated_at"])
    if not locked.company_id or not owner.company_id:
        raise WorkflowValidationError({"owner": ["The developer must belong to an active company."]})
    validate_company_relations(locked.company_id, {"owner": owner})
    if locked.owner_id or locked.ticket_no:
        raise WorkflowValidationError({"detail": ["This ticket is already assigned. Use reassignment instead."]})
    if not owner.is_active or not UserRole.objects.filter(
        user=owner, is_active=True, role__code="DEVELOPER", role__is_active=True, role__is_deleted=False,
    ).exists():
        raise WorkflowValidationError({"owner": ["Choose an active developer."]})
    if locked.bug_id and (locked.bug.owner_id or locked.bug.status != "NEW"):
        raise WorkflowValidationError({"detail": ["This bug has already entered the work flow."]})
    if locked.bug_id and ticket_type != TicketType.BUG:
        locked.bug.soft_delete(deleted_by=actor.unique_id)
        locked.bug = None
    previous_type = locked.ticket_type
    locked.ticket_type = ticket_type
    locked.needs_review = False
    locked.classification_status = ClassificationStatus.HUMAN_CONFIRMED
    locked.confirmed_by = actor
    locked.confirmed_at = timezone.now()
    locked.owner = owner
    locked.status = TicketStatus.PENDING_APPROVAL if ticket_type == TicketType.ACCESS_REQUEST else TicketStatus.ASSIGNED
    locked.ticket_no = generate_ticket_no(local_today())
    locked.updated_by = actor.unique_id
    if ticket_type == TicketType.BUG and not locked.bug_id:
        from apps.bugs.services.bug_service import create_bug

        locked.bug = create_bug(
            actor=actor, company=locked.company, reported_by=locked.reported_by, project=locked.project,
            module=locked.module, submodule=locked.submodule, priority=locked.priority,
            severity=locked.severity, title=locked.title, description=locked.description or locked.title,
            request=request,
        )
    locked.save()
    if locked.bug_id:
        from apps.bugs.services.assignment_service import assign_bug

        assign_bug(bug=locked.bug, new_owner=owner, actor=actor, request=request)
    else:
        record_activity(
            ticket=locked, event_type="TICKET_ASSIGNED", title="Ticket Assigned",
            description=f"Assigned to {owner.display_name} by {actor.display_name}.",
            public_description="Your request has been assigned to a support specialist.", actor=actor,
        )
        from apps.notifications.models import NotificationType
        from apps.notifications.services import notify

        notify(recipients=[owner], notification_type=NotificationType.TICKET_ASSIGNED,
               title=f"{locked.reference} assigned to you", message=locked.title,
               ticket=locked, link=f"/tickets?ticket={locked.unique_id}", actor=actor)
    TicketAssignmentHistory.objects.create(ticket=locked, to_owner=owner, performed_by=actor)
    record_audit(action=AuditAction.TICKET_ASSIGNED, entity=locked, actor=actor,
                 new_value=owner.display_name, request=request)
    if previous_type != ticket_type:
        record_audit(action=AuditAction.TICKET_TYPE_CHANGED, entity=locked, actor=actor,
                     old_value=previous_type, new_value=ticket_type, request=request)
    return locked
