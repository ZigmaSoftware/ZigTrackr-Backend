"""Owner assignment and reassignment (spec 39.5)."""

from django.db import transaction

from apps.audit.models import AuditAction
from apps.bugs.constants import BugStatus
from apps.bugs.models import BugAssignmentHistory
from apps.bugs.services.status_service import change_status
from common.services.audit import record_audit
from common.utils.dates import local_now


@transaction.atomic
def assign_bug(*, bug, new_owner, actor, remarks="", expected_closure_date=None,
               request=None):
    """Assign or reassign, appending to the immutable assignment history.

    A bug in NEW moves to ASSIGNED automatically: assigning an owner *is* the
    NEW -> ASSIGNED transition in spec 29, and requiring two API calls to
    express one business action invites states where a bug has an owner but
    still reads as New.
    """
    previous_owner = bug.owner

    bug.owner = new_owner
    bug.assigned_by = actor
    bug.assigned_date = local_now()
    fields = ["owner", "assigned_by", "assigned_date", "updated_by", "updated_at"]

    if expected_closure_date is not None:
        bug.expected_closure_date = expected_closure_date
        fields.append("expected_closure_date")

    bug.updated_by = getattr(actor, "unique_id", None)
    bug.save(update_fields=fields)

    BugAssignmentHistory.objects.create(
        bug=bug, from_owner=previous_owner, to_owner=new_owner,
        assigned_by=actor, remarks=remarks,
    )

    record_audit(
        action=AuditAction.REASSIGN if previous_owner else AuditAction.ASSIGN,
        entity=bug, actor=actor, field_name="owner",
        old_value=previous_owner, new_value=new_owner,
        remarks=remarks, request=request,
    )

    # Spec 29: NEW -> ASSIGNED and REOPENED -> ASSIGNED are both legal, and in
    # both cases assigning an owner *is* the transition, not a separate step.
    # Without this, a reassigned reopened bug sat at REOPENED with an owner --
    # the exact "owner but wrong status" inconsistency this function exists to
    # avoid; it just did not originally cover the reopen case.
    if bug.status in (BugStatus.NEW, BugStatus.REOPENED):
        change_status(bug=bug, to_status=BugStatus.ASSIGNED, actor=actor,
                      remarks=remarks or f"Assigned to {new_owner.display_name}.",
                      request=request)

    from apps.tickets.models import SupportTicket
    from apps.tickets.services.activity_service import record_activity

    ticket = SupportTicket.objects.filter(bug=bug, is_deleted=False).first()
    if ticket:
        ticket.owner = new_owner
        ticket.save(update_fields=["owner", "updated_at"])
        if previous_owner:
            description = (f"Assigned from {previous_owner.display_name} to "
                           f"{new_owner.display_name} by {actor.display_name}.")
            if remarks:
                description += f" Reason: {remarks}"
        else:
            description = f"Assigned to {new_owner.display_name} by {actor.display_name}."
        record_activity(
            ticket=ticket,
            event_type="TICKET_REASSIGNED" if previous_owner else "TICKET_ASSIGNED",
            title="Ticket Reassigned" if previous_owner else "Ticket Assigned",
            description=description,
            public_description="Your request has been assigned to a support specialist.",
            actor=actor,
        )

    return bug
