"""Requester thread details show each new message, not its quoted history."""

from django.test import TestCase
from django.utils import timezone

from apps.mail_intake.models import MailIntake
from apps.tickets.selectors.queries import base_ticket_queryset
from apps.tickets.serializers.ticket import SupportTicketListSerializer
from apps.tickets.services.conversation_service import request_messages
from apps.tickets.tests.factories import make_ticket


class RequestConversationTests(TestCase):
    def test_original_precedes_cleaned_reply(self):
        ticket = make_ticket()
        original = MailIntake.objects.create(
            dedupe_key="thread-original", from_email="sender@example.com",
            subject="Login issue", body_text="Login fails on submit.",
            received_at=timezone.now(), linked_ticket=ticket,
        )
        MailIntake.objects.create(
            dedupe_key="thread-reply", from_email="sender@example.com",
            subject="Re: Login issue",
            body_text="Still failing after retry.\n\nOn Monday, Support wrote:\nLogin fails on submit.",
            received_at=timezone.now(), linked_ticket=ticket, is_thread_reply=True,
        )

        rows = request_messages(ticket)
        self.assertEqual([row["kind"] for row in rows], ["ORIGINAL", "REPLY"])
        self.assertEqual(rows[0]["body"], "Login fails on submit.")
        self.assertEqual(rows[1]["body"], "Still failing after retry.")
        self.assertNotIn("Support wrote", rows[1]["body"])

    def test_ticket_list_mail_fields_use_mail_side_link(self):
        ticket = make_ticket()
        original = MailIntake.objects.create(
            dedupe_key="list-original", from_email="sender@example.com",
            to_emails=["support@example.com"], received_at=timezone.now(),
            linked_ticket=ticket,
        )
        MailIntake.objects.create(
            dedupe_key="list-reply", from_email="sender@example.com",
            received_at=timezone.now(), linked_ticket=ticket, is_thread_reply=True,
        )

        row = base_ticket_queryset().get(pk=ticket.pk)
        data = SupportTicketListSerializer(row).data
        self.assertEqual(data["mail_id"], str(original.unique_id))
        self.assertEqual(data["mail_from_email"], "sender@example.com")
        self.assertEqual(data["mail_to_email"], "support@example.com")
        self.assertEqual(
            base_ticket_queryset().order_by("original_mail_received_at").first().pk,
            ticket.pk,
        )
