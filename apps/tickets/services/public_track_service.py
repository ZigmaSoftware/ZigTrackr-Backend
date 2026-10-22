"""Ticket-scoped, short-lived verification for the public tracking page."""

import hmac
from datetime import timedelta

from django.conf import settings
from django.core import signing
from django.db import transaction
from django.http import Http404
from django.db.models import Max, OuterRef, Q, Subquery
from django.utils import timezone

from apps.tickets.constants import TicketStatus, TicketType
from apps.tickets.models import SupportTicket, TicketActivity, TicketReopenHistory
from apps.tickets.services.activity_service import record_activity
from apps.tickets.services.policy import chat_state, effective_status
from common.exceptions.domain import WorkflowValidationError

COOKIE_NAME = "zigtrackr_public_ticket"
COOKIE_PATH = "/api/v1/tickets/public/track/"
TOKEN_SALT = "zigtrackr.public-track.v1"
TOKEN_MAX_AGE = 30 * 60
REOPEN_WINDOW = timedelta(days=2)


def closure_state(ticket):
    """Confirmation belongs to the latest closure, never an earlier work cycle."""
    if hasattr(ticket, "tracking_closed_at"):
        closed_at = ticket.tracking_closed_at
        confirmed_at = ticket.tracking_confirmed_at
    else:
        dates = ticket.activities.aggregate(
            closed_at=Max("occurred_at", filter=Q(event_type__in=("TICKET_CLOSED", "BUG_STATUS_CLOSED"))),
            confirmed_at=Max("occurred_at", filter=Q(event_type="TICKET_REQUESTER_CLOSED")),
        )
        closed_at, confirmed_at = dates["closed_at"], dates["confirmed_at"]
    closed_at = (ticket.bug.closed_at if ticket.bug_id else None) or closed_at
    return closed_at, bool(closed_at and confirmed_at and confirmed_at >= closed_at)


def reopen_deadline(ticket):
    """Use the latest actual closure, not updated_at (which later edits can move)."""
    closed_at, confirmed = closure_state(ticket)
    return closed_at + REOPEN_WINDOW if closed_at and not confirmed else None


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


def set_track_cookie(response, email):
    """Issue a session scoped to a verified EMAIL, not a single ticket.

    The address is the identity the passcode proved, so one sign-in covers
    every ticket that address owns. Pinning a ticket_id here instead would
    mean re-verifying to switch between your own tickets.
    """
    value = signing.dumps(
        {"email": email.strip().lower()}, salt=TOKEN_SALT, compress=True,
    )
    response.set_cookie(
        COOKIE_NAME, value, max_age=TOKEN_MAX_AGE, path=COOKIE_PATH,
        httponly=True, secure=settings.AUTH_COOKIE_SECURE, samesite="Lax",
    )


def verified_email(request):
    """The address proved by the current session, or 404."""
    try:
        data = signing.loads(
            request.COOKIES.get(COOKIE_NAME, ""), salt=TOKEN_SALT, max_age=TOKEN_MAX_AGE,
        )
        email = (data["email"] or "").strip().lower()
    except (signing.BadSignature, KeyError, TypeError, ValueError):
        raise Http404
    if not email:
        raise Http404
    return email


def _latest_activity_time(*event_types):
    return Subquery(TicketActivity.objects.filter(
        ticket_id=OuterRef("pk"), event_type__in=event_types,
    ).order_by("-occurred_at", "-id").values("occurred_at")[:1])


def tickets_for_email(email):
    """Every assigned ticket belonging to this address, newest first.

    Filtered on ticket_no being set: a ticket still at the REF intake stage
    has not been accepted, has no owner and no chat, so there is nothing for
    a requester to track yet.
    """
    return (
        SupportTicket.objects.filter(
            reported_by_email__iexact=email, is_deleted=False,
        )
        .exclude(ticket_no__isnull=True)
        .exclude(ticket_no="")
        .select_related("bug", "owner")
        .annotate(
            tracking_closed_at=_latest_activity_time("TICKET_CLOSED", "BUG_STATUS_CLOSED"),
            tracking_confirmed_at=_latest_activity_time("TICKET_REQUESTER_CLOSED"),
            tracking_returned_at=_latest_activity_time("TICKET_RETURNED_TO_DEVELOPER"),
            tracking_assigned_at=_latest_activity_time("TICKET_ASSIGNED", "TICKET_REASSIGNED"),
        )
        .order_by("-created_at", "-id")
    )


def verified_ticket(request, ticket_no=None):
    """Authorise one ticket against the session's verified address.

    ticket_no comes from the caller (query string or body). It is never
    trusted on its own: ownership is always re-checked against the address in
    the signed cookie, so a guessed number buys nothing. With no ticket_no the
    most recent ticket is returned, which keeps single-ticket callers working.
    """
    email = verified_email(request)
    queryset = tickets_for_email(email)
    if ticket_no:
        ticket = queryset.filter(ticket_no__iexact=str(ticket_no).strip()).first()
    else:
        ticket = queryset.first()
    if ticket is None:
        raise Http404
    # Belt and braces: the queryset already filters on the address, but the
    # comparison is what the original code relied on, so it stays.
    if not hmac.compare_digest(
        (ticket.reported_by_email or "").strip().lower(), email
    ):
        raise Http404
    return ticket


def public_ticket_data(ticket):
    from apps.tickets.views.public_views import PUBLIC_STATUS_LABELS
    from apps.tickets.services.public_progress import LABELS, public_stage, ticket_progress

    status = effective_status(ticket)
    deadline = reopen_deadline(ticket) if status == TicketStatus.CLOSED else None
    can_confirm = bool(deadline and timezone.now() < deadline)
    stage = public_stage(status, awaiting_confirmation=can_confirm)
    progress = ticket_progress(ticket)
    if status == TicketStatus.ASSIGNED and any(
        row["code"] == "RESENT" and row["state"] == "current" for row in progress
    ):
        stage = "RESENT"
    return {
        "found": True,
        "ticket_no": ticket.ticket_no,
        "subject": ticket.title,
        "owner_name": ticket.owner.display_name if ticket.owner_id else None,
        "status": status,
        "status_label": LABELS.get(stage, PUBLIC_STATUS_LABELS.get(status, ticket.get_status_display())),
        "public_stage": stage,
        "can_reopen": can_confirm,
        "can_confirm_close": can_confirm,
        "auto_closes_at": deadline if can_confirm else None,
        "progress": progress,
        "chat_state": chat_state(ticket, requester_email=ticket.reported_by_email),
        # Staff messages the requester has not read. Lives on the ticket
        # payload, not the chat payload, because the Chat tab only loads its
        # messages once opened -- the badge has to be answerable while the
        # requester is still looking at the Timeline.
        "unread_count": ticket.chat_messages.filter(
            sender_type="STAFF", read_at__isnull=True, is_deleted=False,
        ).count(),
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


@transaction.atomic
def confirm_public_ticket_closed(*, ticket):
    locked = SupportTicket.objects.select_for_update().select_related("bug", "owner").get(pk=ticket.pk)
    if effective_status(locked) != TicketStatus.CLOSED:
        raise WorkflowValidationError({"detail": ["Only a resolved ticket can be confirmed as fixed."]})
    closed_at, confirmed = closure_state(locked)
    if confirmed:
        return locked
    deadline = reopen_deadline(locked)
    if not deadline or timezone.now() >= deadline:
        raise WorkflowValidationError({"detail": ["This ticket is already closed."]})
    record_activity(
        ticket=locked, event_type="TICKET_REQUESTER_CLOSED", title="Issue confirmed fixed",
        public_description="You confirmed the issue is fixed and closed this ticket.",
        description="The requester confirmed the fix and ended the reopening window.",
        actor_email=locked.reported_by_email,
        source_key=f"requester-close:{locked.unique_id}:{closed_at.isoformat()}",
    )
    return locked


def public_ticket_list(email):
    """Rows for the signed-in requester's ticket list.

    Deliberately lighter than public_ticket_data: the list needs enough to
    choose between tickets, not the full detail payload. Unread counts are
    annotated in one query rather than per row, so the list stays a single
    round trip however many tickets the address owns.
    """
    from django.db.models import Count, Q

    from apps.tickets.views.public_views import PUBLIC_STATUS_LABELS

    rows = tickets_for_email(email).annotate(
        unread=Count(
            "chat_messages", distinct=True,
            filter=Q(
                chat_messages__sender_type="STAFF",
                chat_messages__read_at__isnull=True,
                chat_messages__is_deleted=False,
            ),
        ),
        last_message_at=Max(
            "chat_messages__created_at",
            filter=Q(chat_messages__is_deleted=False),
        ),
    )
    payload = []
    from apps.tickets.services.public_progress import LABELS, public_stage

    now = timezone.now()
    for ticket in rows:
        status = effective_status(ticket)
        deadline = reopen_deadline(ticket) if status == TicketStatus.CLOSED else None
        stage = public_stage(status, awaiting_confirmation=bool(deadline and now < deadline))
        if status == TicketStatus.ASSIGNED and ticket.tracking_returned_at and (
            not ticket.tracking_assigned_at or ticket.tracking_returned_at >= ticket.tracking_assigned_at
        ):
            stage = "RESENT"
        payload.append({
            "ticket_no": ticket.ticket_no,
            "subject": ticket.title,
            "status": status,
            "status_label": LABELS.get(stage, PUBLIC_STATUS_LABELS.get(status, ticket.get_status_display())),
            "public_stage": stage,
            "owner_name": ticket.owner.display_name if ticket.owner_id else None,
            "created_at": ticket.created_at,
            "last_message_at": ticket.last_message_at,
            "unread_count": ticket.unread,
        })
    return payload
