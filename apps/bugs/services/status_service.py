"""Status transitions -- the only path that writes Bug.status."""

from django.db import transaction

from apps.audit.models import AuditAction
from apps.bugs.constants import BugStatus
from apps.bugs.models import BugStatusHistory
from apps.bugs.services import workflow
from apps.bugs.services.update_service import add_update
from common.services.audit import record_audit
from common.utils.dates import local_now

# Fields a transition is allowed to set on the bug as part of the move.
TRANSITION_FIELDS = {
    BugStatus.ON_HOLD: ("hold_reason",),
    BugStatus.PENDING: ("pending_reason",),
    BugStatus.REJECTED: ("rejection_reason",),
}


@transaction.atomic
def change_status(*, bug, to_status, actor, remarks="", payload=None, request=None,
                  write_update=True, allow_reopened_verification=False):
    """Validate and apply a status change, recording immutable history.

    The system-generated daily update is what makes a status change count as
    that day's update (spec 11) -- the owner did something meaningful, so the
    Team Lead's pending list should not flag the bug. BugUpdate.is_system_generated
    marks it so that rule can be tightened later without a migration.
    """
    payload = payload or {}
    from_status = bug.status

    workflow.validate_transition(
        bug, to_status, payload=payload,
        allow_reopened_verification=allow_reopened_verification,
    )

    if to_status == BugStatus.IN_PROGRESS:
        from apps.tickets.models import SupportTicket
        from apps.tickets.services.policy import ensure_owner_can_start

        linked_ticket = SupportTicket.objects.filter(bug=bug, is_deleted=False).first()
        if linked_ticket and linked_ticket.owner_id:
            ensure_owner_can_start(linked_ticket.owner_id, exclude_ticket_id=linked_ticket.pk)

    # Apply any reason fields that belong to this transition.
    changed_fields = ["status", "updated_at", "updated_by"]
    for field in TRANSITION_FIELDS.get(to_status, ()):
        if field in payload:
            setattr(bug, field, payload[field])
            changed_fields.append(field)

    bug.status = to_status
    bug.updated_by = getattr(actor, "unique_id", None)
    bug.save(update_fields=changed_fields)

    BugStatusHistory.objects.create(
        bug=bug, from_status=from_status, to_status=to_status,
        remarks=remarks, changed_by=actor,
    )

    record_audit(action=AuditAction.STATUS_CHANGE, entity=bug, actor=actor,
                 field_name="status", old_value=BugStatus(from_status).label,
                 new_value=BugStatus(to_status).label, remarks=remarks,
                 request=request)

    if write_update:
        add_update(
            bug=bug, actor=actor,
            update_text=(remarks or
                         f"Status changed from {BugStatus(from_status).label} "
                         f"to {BugStatus(to_status).label}."),
            is_system_generated=True, request=request,
        )

    from apps.tickets.models import SupportTicket
    from apps.tickets.services.activity_service import record_activity

    ticket = SupportTicket.objects.filter(bug=bug, is_deleted=False).first()
    if ticket and to_status != BugStatus.ASSIGNED:
        titles = {
            BugStatus.IN_PROGRESS: "Work started" if from_status != BugStatus.TESTING
            else "Returned to Developer",
            BugStatus.PENDING: "Ticket moved to Pending",
            BugStatus.ON_HOLD: "Ticket placed on Hold",
            BugStatus.TESTING: "Issue Rectified",
            BugStatus.RESOLVED: "Verification passed",
            BugStatus.CLOSED: "Ticket Closed",
            BugStatus.REOPENED: "Ticket reopened",
            BugStatus.REJECTED: "Ticket rejected",
        }
        public_text = {
            BugStatus.IN_PROGRESS: "Your request is being worked on.",
            BugStatus.PENDING: "Your request is pending further information.",
            BugStatus.ON_HOLD: "Your request is on hold.",
            BugStatus.TESTING: "The issue was rectified and moved to verification.",
            BugStatus.RESOLVED: "The correction passed verification.",
            BugStatus.CLOSED: "Your request was verified and closed.",
            BugStatus.REOPENED: "Your request was reopened for additional work.",
            BugStatus.REJECTED: "Your request was closed.",
        }
        requester_action = bool(
            request and getattr(request, "user", None)
            and not request.user.is_authenticated and to_status == BugStatus.REOPENED
        )
        record_activity(
            ticket=ticket, event_type=f"BUG_STATUS_{to_status}",
            title=titles.get(to_status, f"Ticket moved to {BugStatus(to_status).label}"),
            description=(f"{actor.display_name} changed status from {from_status} "
                         f"to {to_status}. {remarks}").strip(),
            public_description=public_text.get(to_status, ""),
            actor=None if requester_action else actor,
            actor_email=ticket.reported_by_email if requester_action else "",
        )

    return bug
