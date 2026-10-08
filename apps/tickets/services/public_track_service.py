"""Ticket-scoped, short-lived verification for the public tracking page."""

import hmac
from datetime import timedelta

from django.conf import settings
from django.core import signing
from django.db import transaction
from django.http import Http404
from django.utils import timezone

from apps.tickets.constants import TicketStatus, TicketType
from apps.tickets.models import SupportTicket, TicketReopenHistory
from apps.tickets.services.activity_service import record_activity
from apps.tickets.services.policy import chat_state, effective_status
from common.exceptions.domain import WorkflowValidationError

COOKIE_NAME = "zigtrackr_public_ticket"
COOKIE_PATH = "/api/v1/tickets/public/track/"
TOKEN_SALT = "zigtrackr.public-track.v1"
TOKEN_MAX_AGE = 20 * 60
REOPEN_WINDOW = timedelta(days=2)


def reopen_deadline(ticket):
    """Use the latest actual closure, not updated_at (which later edits can move)."""
    closed_at = ticket.bug.closed_at if ticket.bug_id else None
    if closed_at is None:
        closed_at = (ticket.activities.filter(event_type="TICKET_CLOSED")
                     .order_by("-occurred_at", "-id")
                     .values_list("occurred_at", flat=True).first())
    return closed_at + REOPEN_WINDOW if closed_at else None


def matching_ticket(ticket_no, email):
    ticket = (
        SupportTicket.objects.filter(ticket_no__iexact=ticket_no.strip(), is_deleted=False)
        .select_related("bug", "owner").first()
    )
    if not ticket or not hmac.compare_digest(
        (ticket.reported_by_email or "").strip().lower(), email.strip().lower()
    ):
        return None
    return ticket


def set_track_cookie(response, ticket):
    value = signing.dumps(
        {"ticket_id": ticket.pk, "email": ticket.reported_by_email.strip().lower()},
        salt=TOKEN_SALT, compress=True,
    )
    response.set_cookie(
        COOKIE_NAME, value, max_age=TOKEN_MAX_AGE, path=COOKIE_PATH,
        httponly=True, secure=settings.AUTH_COOKIE_SECURE, samesite="Lax",
    )


def verified_ticket(request):
    try:
        data = signing.loads(
            request.COOKIES.get(COOKIE_NAME, ""), salt=TOKEN_SALT, max_age=TOKEN_MAX_AGE,
        )
        ticket = SupportTicket.objects.select_related("bug", "owner").get(
            pk=data["ticket_id"], is_deleted=False,
        )
    except (signing.BadSignature, KeyError, SupportTicket.DoesNotExist, TypeError, ValueError):
        raise Http404
    if not hmac.compare_digest(
        (ticket.reported_by_email or "").strip().lower(), data["email"]
    ):
        raise Http404
    return ticket


def public_ticket_data(ticket):
    from apps.tickets.views.public_views import PUBLIC_STATUS_LABELS

    status = effective_status(ticket)
    deadline = reopen_deadline(ticket) if status == TicketStatus.CLOSED else None
    return {
        "found": True,
        "ticket_no": ticket.ticket_no,
        "subject": ticket.title,
        "owner_name": ticket.owner.display_name if ticket.owner_id else None,
        "status": status,
        "status_label": PUBLIC_STATUS_LABELS.get(status, ticket.get_status_display()),
        "can_reopen": bool(deadline and timezone.now() < deadline),
        "chat_state": chat_state(ticket, requester_email=ticket.reported_by_email),
    }


@transaction.atomic
def reopen_public_ticket(*, ticket, reason, request=None):
    reason = (reason or "").strip()
    if not reason or len(reason) > 2000:
        raise WorkflowValidationError({"reason": ["Give a reason for reopening (up to 2000 characters)."]})
    locked = SupportTicket.objects.select_for_update().select_related("bug", "owner").get(pk=ticket.pk)
    if effective_status(locked) != TicketStatus.CLOSED:
        raise WorkflowValidationError({"detail": ["Only a closed ticket can be reopened."]})
    deadline = reopen_deadline(locked)
    if deadline is None or timezone.now() >= deadline:
        raise WorkflowValidationError({"detail": [
            "The 2-day reopening period has ended. Please submit a new request."
        ]})

    if locked.bug_id:
        from apps.accounts.services.system_user import get_mail_intake_user
        from apps.bugs.services.closure_service import reopen_bug

        reopen_bug(
            bug=locked.bug, actor=get_mail_intake_user(), reopen_reason=reason, request=request,
        )
        locked.status = locked.bug.status
    elif locked.ticket_type == TicketType.ACCESS_REQUEST:
        locked.status = TicketStatus.PENDING_APPROVAL
    else:
        locked.status = TicketStatus.REOPENED
    locked.save(update_fields=["status", "updated_at"])
    TicketReopenHistory.objects.create(
        ticket=locked, requester_email=locked.reported_by_email,
        reason=reason, previous_status=TicketStatus.CLOSED,
    )
    if not locked.bug_id:
        record_activity(
            ticket=locked, event_type="TICKET_REOPENED", title="Ticket reopened",
            description=f"The requester reopened the ticket. Reason: {reason}",
            public_description=f"You reopened this ticket. Reason: {reason}",
            actor_email=locked.reported_by_email,
        )
    return locked
