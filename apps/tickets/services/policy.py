"""Ticket action availability shared by API responses and domain checks."""

from django.db.models import Q

from apps.tickets.constants import TicketStatus
from common.permissions.require import has_permission
from common.permissions.scoping import can_mutate_ticket


# A ticket remains routable until work is handed to verification. Failed tests
# return it to IN_PROGRESS; requester reopenings return it to REOPENED.
REASSIGNABLE_STATUSES = (
    TicketStatus.NEW,
    TicketStatus.CONFIRMED,
    TicketStatus.ASSIGNED,
    TicketStatus.IN_PROGRESS,
    TicketStatus.PENDING,
    TicketStatus.ON_HOLD,
    TicketStatus.REOPENED,
    TicketStatus.PENDING_APPROVAL,
    TicketStatus.APPROVED,
)


def effective_status(ticket):
    return ticket.bug.status if ticket.bug_id else ticket.status


def active_ticket_for_owner(owner_id, *, exclude_ticket_id=None, for_update=False):
    """Find another active work item, respecting a linked bug's source-of-truth status."""
    from apps.tickets.models import SupportTicket

    base = SupportTicket.objects.filter(owner_id=owner_id, is_deleted=False)
    if exclude_ticket_id is not None:
        base = base.exclude(pk=exclude_ticket_id)
    # Separate queries keep FOR UPDATE away from a nullable OUTER JOIN on MariaDB.
    for tickets in (
        base.filter(bug__isnull=True, status=TicketStatus.IN_PROGRESS),
        base.filter(bug__status=TicketStatus.IN_PROGRESS),
    ):
        if for_update:
            tickets = tickets.select_for_update()
        active = tickets.order_by("pk").first()
        if active:
            return active
    return None


def ensure_owner_can_start(owner_id, *, exclude_ticket_id):
    """Serialize developer starts and reject a second active ticket."""
    from apps.accounts.models import User, UserRole
    from common.exceptions.domain import WorkflowValidationError

    User.objects.select_for_update().get(pk=owner_id)
    if not UserRole.objects.filter(
        user_id=owner_id, is_active=True, role__code="DEVELOPER",
        role__is_active=True, role__is_deleted=False,
    ).exists():
        return
    active = active_ticket_for_owner(owner_id, exclude_ticket_id=exclude_ticket_id, for_update=True)
    if active:
        raise WorkflowValidationError({"detail": [
            f"Finish or pause {active.reference} before starting another ticket."
        ]})


def reassign_block_reason(ticket):
    if not ticket.owner_id:
        return "Assign the ticket before reassigning it."
    status = effective_status(ticket)
    if status == TicketStatus.TESTING:
        return ("This ticket has already been rectified and moved to verification. "
                "Complete the testing workflow or return it to development before reassignment.")
    if status in (TicketStatus.CLOSED, TicketStatus.REJECTED):
        return ("Closed tickets cannot be reassigned. Reopen the ticket through the controlled "
                "reopen workflow if further work is required.")
    return "Reassignment is available only before rectification or after work returns to development."


def can_reassign(ticket, user):
    return bool(
        user and user.is_authenticated
        and has_permission(user, "tickets.ticket.reassign")
        and can_mutate_ticket(user, ticket)
        and ticket.owner_id
        and effective_status(ticket) in REASSIGNABLE_STATUSES
    )


def reassignable_ticket_queryset(queryset, user):
    """Database equivalent of can_reassign, applied before list pagination."""
    if not user or not user.is_authenticated or not has_permission(user, "tickets.ticket.reassign"):
        return queryset.none()

    queryset = queryset.filter(owner__isnull=False).filter(
        Q(bug__isnull=True, status__in=REASSIGNABLE_STATUSES)
        | Q(bug__status__in=REASSIGNABLE_STATUSES)
    )
    if user.is_superuser or has_permission(user, "tickets.ticket.mutate_all"):
        return queryset

    mutable = Q(owner=user) | Q(reported_by=user)
    if has_permission(user, "tickets.ticket.classify"):
        mutable |= Q(needs_review=True)
        if user.team_id:
            mutable |= Q(owner__team_id=user.team_id)
    if has_permission(user, "tickets.ticket.verify_close"):
        mutable |= Q(bug__status=TicketStatus.REOPENED) | Q(
            bug__isnull=True, status=TicketStatus.REOPENED
        )
    return queryset.filter(mutable)


def chat_state(ticket, user=None, requester_email=""):
    status = effective_status(ticket)
    can_view = bool(requester_email) or bool(user and user.is_authenticated)
    if not ticket.owner_id:
        return {"can_view": can_view, "can_send": False, "reason": "Chat opens after assignment."}
    if status in (TicketStatus.CLOSED, TicketStatus.REJECTED):
        return {"can_view": can_view, "can_send": False, "reason": "This ticket is closed. Chat is read-only."}
    if status in (TicketStatus.TESTING, "RESOLVED", TicketStatus.COMPLETED):
        return {"can_view": can_view, "can_send": False,
                "reason": "Chat is temporarily closed while the ticket is being verified."}
    if requester_email:
        allowed = requester_email.strip().lower() == ticket.reported_by_email.strip().lower()
    else:
        allowed = bool(
            user and user.is_authenticated
            and (ticket.owner_id == user.pk
                 or has_permission(user, "tickets.ticket.mutate_all")
                 or has_permission(user, "tickets.ticket.classify"))
            and can_mutate_ticket(user, ticket)
        )
    return {"can_view": can_view, "can_send": allowed,
            "reason": None if allowed else "You cannot send messages on this ticket."}
