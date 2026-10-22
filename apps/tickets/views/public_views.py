"""Public ticket status lookup.

The only unauthenticated endpoint in the tickets app, reached from the link in
the assignment email. Three rules shape it:

  1. Ticket number AND the requester's email must both match. Numbers are
     sequential, so a number alone is guessable; requiring the address the
     request came from means a guess reveals nothing.
  2. One generic "not found" for every failure -- wrong number, wrong email,
     soft-deleted ticket. Distinguishing them would turn the endpoint into an
     oracle for which ticket numbers and addresses exist.
  3. Status, dates and progress notes only. No description, no internal
     remarks, no owner name: the requester is told where their request stands,
     not what was said about it internally.
"""

from django.utils.decorators import method_decorator
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_protect
from rest_framework import serializers
from rest_framework.permissions import AllowAny
from rest_framework.throttling import AnonRateThrottle
from rest_framework.views import APIView

from apps.tickets.constants import TicketStatus
from apps.tickets.models import SupportTicket
from apps.tickets.services.chat_service import (
    acknowledge_messages, change_message, message_role_labels, send_message, serialize_message,
)
from apps.tickets.services.public_track_service import (
    COOKIE_NAME, COOKIE_PATH, confirm_public_ticket_closed,
    public_ticket_data, public_ticket_list, reopen_public_ticket,
    set_track_cookie, verified_email, verified_ticket,
)
from apps.tickets.services.ticket_otp_service import issue_otp, verify_otp
from apps.tickets.services.timeline_service import build_ticket_timeline
from common.responses import ok
from common.throttling import PublicOtpThrottle, PublicOtpVerifyThrottle


class PublicTicketLookupThrottle(AnonRateThrottle):
    """Guessing a number needs many tries; this makes that impractical."""

    scope = "public_ticket_lookup"


class PublicTicketLookupSerializer(serializers.Serializer):
    ticket_no = serializers.CharField(max_length=40)
    email = serializers.EmailField()


# What a requester is told, in their words rather than the workflow's.
PUBLIC_STATUS_LABELS = {
    TicketStatus.NEW: "Received",
    TicketStatus.NEEDS_REVIEW: "Received",
    TicketStatus.CONFIRMED: "Accepted",
    TicketStatus.ASSIGNED: "Accepted",
    TicketStatus.IN_PROGRESS: "In progress",
    TicketStatus.PENDING: "Pending",
    TicketStatus.ON_HOLD: "On hold",
    TicketStatus.TESTING: "Being verified",
    TicketStatus.PENDING_APPROVAL: "Awaiting approval",
    TicketStatus.APPROVED: "Approved",
    TicketStatus.COMPLETED: "Completed",
    TicketStatus.CLOSED: "Closed",
    TicketStatus.REJECTED: "Closed",
    "REOPENED": "Reopened",
}


@method_decorator(never_cache, name="dispatch")
class PublicTicketLookupView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [PublicTicketLookupThrottle]

    def post(self, request):
        serializer = PublicTicketLookupSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        ticket_no = serializer.validated_data["ticket_no"].strip().upper()
        email = serializer.validated_data["email"].strip().lower()

        ticket = (
            SupportTicket.objects
            .filter(ticket_no__iexact=ticket_no, is_deleted=False)
            .select_related("bug")
            .first()
        )
        # One shape of answer for every failure (rule 2).
        if ticket is None or (ticket.reported_by_email or "").strip().lower() != email:
            return ok({"found": False}, message="No matching ticket.")

        status_value = getattr(ticket.bug, "status", None) or ticket.status
        return ok({
            "found": True,
            "ticket_no": ticket.ticket_no,
            "subject": ticket.title,
            "status": status_value,
            "status_label": PUBLIC_STATUS_LABELS.get(status_value, "In progress"),
            "raised_on": ticket.created_at,
            "expected_closure": (
                getattr(ticket.bug, "expected_closure_date", None)
                or ticket.expected_closure_date
            ),
            "last_updated": ticket.updated_at,
            "is_closed": status_value in (TicketStatus.CLOSED, TicketStatus.REJECTED),
        }, message="Ticket found.")


class PublicOtpRequestSerializer(serializers.Serializer):
    """Sign-in asks only for the address; the code proves it."""

    email = serializers.EmailField()


class PublicOtpVerifySerializer(PublicOtpRequestSerializer):
    code = serializers.CharField(min_length=6, max_length=6, trim_whitespace=True)


@method_decorator(csrf_protect, name="dispatch")
@method_decorator(never_cache, name="dispatch")
class PublicTrackRequestOtpView(APIView):
    """Step 1 of 2: email a passcode if the address owns any ticket.

    The response is byte-identical whether or not it does. That is the whole
    point -- a differing answer would turn this into an oracle for which
    addresses have raised tickets, which is exactly what the passcode is
    meant to protect.
    """

    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [PublicOtpThrottle]

    def post(self, request):
        serializer = PublicOtpRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        issue_otp(email=serializer.validated_data["email"], request=request)
        # No cookie is set here: possession of the mailbox is not yet proven.
        return ok(
            {"otp_sent": True},
            message="If that address has any tickets, we've emailed a code.",
        )


@method_decorator(csrf_protect, name="dispatch")
@method_decorator(never_cache, name="dispatch")
class PublicTrackVerifyOtpView(APIView):
    """Step 2 of 2: the only place a tracking session is issued."""

    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [PublicOtpVerifyThrottle]

    def post(self, request):
        serializer = PublicOtpVerifySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        # Raises WorkflowValidationError (400) on a bad or expired code, so a
        # failure can never fall through to the cookie below.
        email = verify_otp(
            email=serializer.validated_data["email"],
            code=serializer.validated_data["code"],
            request=request,
        )
        response = ok(
            {"email": email, "tickets": public_ticket_list(email)},
            message="Signed in.",
        )
        set_track_cookie(response, email)
        return response



def _ticket_no(request):
    """Which of the caller's tickets this request is about.

    Read from the query string or body, never trusted on its own:
    verified_ticket re-checks ownership against the address in the signed
    cookie, so a guessed number is worthless. Absent means "the most recent",
    which keeps older single-ticket clients working.
    """
    return (
        request.query_params.get("ticket_no")
        or (request.data.get("ticket_no") if isinstance(request.data, dict) else None)
        or ""
    )


@method_decorator(never_cache, name="dispatch")
class PublicTrackListView(APIView):
    """Every ticket belonging to the signed-in address."""

    authentication_classes = []
    permission_classes = [AllowAny]

    def get(self, request):
        email = verified_email(request)
        return ok({"email": email, "tickets": public_ticket_list(email)})


@method_decorator(csrf_protect, name="dispatch")
@method_decorator(never_cache, name="dispatch")
class PublicTrackSignoutView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    def post(self, request):
        response = ok(None, message="Signed out.")
        response.delete_cookie(COOKIE_NAME, path=COOKIE_PATH, samesite="Lax")
        return response


@method_decorator(csrf_protect, name="dispatch")
@method_decorator(never_cache, name="dispatch")
class PublicTrackCloseView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    def post(self, request):
        ticket = verified_ticket(request, _ticket_no(request))
        ticket = confirm_public_ticket_closed(ticket=ticket)
        return ok(public_ticket_data(ticket), message="Ticket closed. Thank you for confirming.")

@method_decorator(never_cache, name="dispatch")
class PublicTrackTicketView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    def get(self, request):
        return ok(public_ticket_data(verified_ticket(request, _ticket_no(request))))


@method_decorator(never_cache, name="dispatch")
class PublicTrackActivityView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    def get(self, request):
        ticket = verified_ticket(request, _ticket_no(request))
        return ok(build_ticket_timeline(ticket, public=True))


class PublicTrackChatThrottle(AnonRateThrottle):
    scope = "public_track_chat"


@method_decorator(csrf_protect, name="dispatch")
@method_decorator(never_cache, name="dispatch")
class PublicTrackChatView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [PublicTrackChatThrottle]

    def get(self, request):
        ticket = verified_ticket(request, _ticket_no(request))
        messages = list(ticket.chat_messages.select_related("reply_to_message").order_by("created_at", "id"))
        role_labels = message_role_labels(messages)
        return ok([serialize_message(row, requester_email=ticket.reported_by_email,
                                     role_labels=role_labels) for row in messages])

    def post(self, request):
        ticket = verified_ticket(request, _ticket_no(request))
        serializer = PublicTrackMessageSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        row = send_message(
            ticket=ticket, text=serializer.validated_data["message"],
            requester_email=ticket.reported_by_email,
            reply_to_id=serializer.validated_data.get("reply_to"),
        )
        return ok(serialize_message(row, requester_email=ticket.reported_by_email), message="Message sent.")


@method_decorator(csrf_protect, name="dispatch")
@method_decorator(never_cache, name="dispatch")
class PublicTrackChatActionView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [PublicTrackChatThrottle]

    def post(self, request, message_id):
        ticket = verified_ticket(request, _ticket_no(request))
        row = change_message(ticket=ticket, message_id=message_id,
                             action=request.data.get("action"),
                             requester_email=ticket.reported_by_email,
                             text=request.data.get("message"), emoji=request.data.get("emoji"))
        return ok(serialize_message(row, requester_email=ticket.reported_by_email))


class PublicTrackReceiptSerializer(serializers.Serializer):
    message_ids = serializers.ListField(child=serializers.UUIDField(), allow_empty=False, max_length=200)
    status = serializers.ChoiceField(choices=("delivered", "read"))


@method_decorator(csrf_protect, name="dispatch")
@method_decorator(never_cache, name="dispatch")
class PublicTrackChatReceiptView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [PublicTrackChatThrottle]

    def post(self, request):
        ticket = verified_ticket(request, _ticket_no(request))
        serializer = PublicTrackReceiptSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return ok(acknowledge_messages(ticket=ticket,
                                       requester_email=ticket.reported_by_email,
                                       **serializer.validated_data))


class PublicTrackMessageSerializer(serializers.Serializer):
    message = serializers.CharField(max_length=4000, trim_whitespace=True)
    reply_to = serializers.UUIDField(required=False)


class PublicTrackReopenSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=2000, trim_whitespace=True)


@method_decorator(csrf_protect, name="dispatch")
@method_decorator(never_cache, name="dispatch")
class PublicTrackReopenView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [PublicTrackChatThrottle]

    def post(self, request):
        ticket = verified_ticket(request, _ticket_no(request))
        serializer = PublicTrackReopenSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        ticket = reopen_public_ticket(
            ticket=ticket, reason=serializer.validated_data["reason"], request=request,
        )
        return ok(public_ticket_data(ticket), message="Ticket reopened.")
