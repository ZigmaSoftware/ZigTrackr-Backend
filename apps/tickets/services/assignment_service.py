"""Transactional ticket reassignment with one shared owner change path."""

from django.db import transaction

from apps.audit.models import AuditAction
from apps.tickets.models import SupportTicket, TicketAssignmentHistory
from apps.tickets.services.activity_service import record_activity
from apps.tickets.services.policy import REASSIGNABLE_STATUSES
from common.exceptions.domain import WorkflowValidationError
from common.services.audit import record_audit


@transaction.atomic
def reassign_ticket(*, ticket, new_owner, actor, reason, routing=None, request=None):
    locked = SupportTicket.objects.select_for_update().select_related("bug", "owner").get(pk=ticket.pk)
    effective_status = locked.bug.status if locked.bug_id else locked.status
    if effective_status not in REASSIGNABLE_STATUSES:
        raise WorkflowValidationError({"detail": [
            "A ticket can be reassigned only before rectification or after work returns to development."
        ]})
    if not locked.owner_id:
        raise WorkflowValidationError({"detail": ["Assign the ticket before reassigning it."]})
    if locked.owner_id == new_owner.pk:
        raise WorkflowValidationError({"owner": ["Choose a different assignee."]})
    reason = (reason or "").strip()
    if not reason:
        raise WorkflowValidationError({"reason": ["A reassignment reason is required."]})

    if effective_status == "IN_PROGRESS":
        from apps.accounts.models import User, UserRole
        from apps.tickets.services.policy import active_ticket_for_owner

        User.objects.select_for_update().get(pk=new_owner.pk)
        is_developer = UserRole.objects.filter(
            user_id=new_owner.pk, is_active=True, role__code="DEVELOPER",
            role__is_active=True, role__is_deleted=False,
        ).exists()
        if is_developer:
            active = active_ticket_for_owner(new_owner.pk, exclude_ticket_id=locked.pk, for_update=True)
            if active:
                raise WorkflowValidationError({"owner": [
                    f"This developer must finish or pause {active.reference} before taking another active ticket."
                ]})

    previous = locked.owner
    routing = routing or {}
    current_project = locked.project or getattr(locked.bug, "project", None)
    current_module = locked.module or getattr(locked.bug, "module", None)
    current_submodule = locked.submodule or getattr(locked.bug, "submodule", None)
    if "project" in routing and routing["project"] != current_project:
        routing.setdefault("module", None)
        routing.setdefault("submodule", None)
    if "module" in routing and routing["module"] != current_module:
        routing.setdefault("submodule", None)
    project = routing.get("project", current_project)
    module = routing.get("module", current_module)
    submodule = routing.get("submodule", current_submodule)
    if module and (not project or module.project_id != project.pk):
        raise WorkflowValidationError({"module": [
            "Module does not belong to the selected project."
        ]})
    if submodule and (not module or submodule.module_id != module.pk):
        raise WorkflowValidationError({"submodule": [
            "Submodule does not belong to the selected module."
        ]})
    for field, value in routing.items():
        if field != "severity":
            setattr(locked, field, value)
        if locked.bug_id and field in {
            "project", "module", "submodule", "priority", "severity", "expected_closure_date"
        }:
            setattr(locked.bug, field, value)

    if locked.bug_id:
        from apps.bugs.services.assignment_service import assign_bug

        assign_bug(
            bug=locked.bug, new_owner=new_owner, actor=actor, remarks=reason,
            expected_closure_date=routing.get("expected_closure_date"), request=request,
        )
        if routing:
            locked.bug.save(update_fields=[*routing.keys(), "updated_at"])

    locked.owner = new_owner
    locked.updated_by = actor.unique_id
    locked.save(update_fields=[
        "owner", "updated_by", "updated_at", *[key for key in routing if key != "severity"],
    ])
    TicketAssignmentHistory.objects.create(
        ticket=locked, from_owner=previous, to_owner=new_owner,
        performed_by=actor, reason=reason,
    )
    record_audit(
        action=AuditAction.REASSIGN, entity=locked, actor=actor,
        field_name="owner", old_value=previous.display_name,
        new_value=new_owner.display_name, remarks=reason, request=request,
    )
    if not locked.bug_id:
        record_activity(
            ticket=locked, event_type="TICKET_REASSIGNED", title="Ticket Reassigned",
            description=(f"The ticket was reassigned from {previous.display_name} to "
                         f"{new_owner.display_name} by {actor.display_name}. Reason: {reason}"),
            public_description="Your request has been reassigned to a support specialist.",
            actor=actor,
        )
    return locked
