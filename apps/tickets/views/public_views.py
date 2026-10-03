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
    matching_ticket, public_ticket_data, reopen_public_ticket,
    set_track_cookie, verified_ticket,
)
from apps.tickets.services.timeline_service import build_ticket_timeline
from common.responses import ok


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


@method_decorator(csrf_protect, name="dispatch")
@method_decorator(never_cache, name="dispatch")
class PublicTrackVerifyView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [PublicTicketLookupThrottle]

    def post(self, request):
        serializer = PublicTicketLookupSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        ticket = matching_ticket(
            serializer.validated_data["ticket_no"],
            serializer.validated_data["email"],
        )
        if not ticket:
            response = ok({"found": False}, message="No matching ticket.")
            response.delete_cookie(
                "zigtrackr_public_ticket", path="/api/v1/tickets/public/track/",
            )
            return response
        response = ok(public_ticket_data(ticket), message="Ticket verified.")
        set_track_cookie(response, ticket)
        return response


@method_decorator(never_cache, name="dispatch")
class PublicTrackTicketView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    def get(self, request):
        return ok(public_ticket_data(verified_ticket(request)))


@method_decorator(never_cache, name="dispatch")
class PublicTrackActivityView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    def get(self, request):
        ticket = verified_ticket(request)
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
        ticket = verified_ticket(request)
        messages = list(ticket.chat_messages.select_related("reply_to_message").order_by("created_at", "id"))
        role_labels = message_role_labels(messages)
        return ok([serialize_message(row, requester_email=ticket.reported_by_email,
                                     role_labels=role_labels) for row in messages])

    def post(self, request):
        ticket = verified_ticket(request)
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
        ticket = verified_ticket(request)
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
        ticket = verified_ticket(request)
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
        ticket = verified_ticket(request)
        serializer = PublicTrackReopenSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        ticket = reopen_public_ticket(
            ticket=ticket, reason=serializer.validated_data["reason"], request=request,
        )
        return ok(public_ticket_data(ticket), message="Ticket reopened.")
