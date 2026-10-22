"""Requester thread details show each new message, not its quoted history."""

from django.test import TestCase
from django.utils import timezone

from apps.mail_intake.models import MailIntake
from apps.tickets.selectors.queries import base_ticket_queryset, with_description_messages
from apps.tickets.serializers.ticket import SupportTicketListSerializer
from apps.tickets.services.conversation_service import request_messages
from apps.tickets.tests.factories import make_ticket


class RequestConversationTests(TestCase):
    def test_full_content_preserves_greeting_signature_and_quoted_history(self):
        ticket = make_ticket()
        body = "Dear Team,\n\nLogin is failing.\n\nThanks & Regards,\nDimple\n\nOn Monday, Support wrote:\n> Please retry."
        MailIntake.objects.create(dedupe_key="full-original", linked_ticket=ticket,
            from_email="sender@example.com", body_text=body, received_at=timezone.now())
        self.assertEqual(request_messages(ticket, full_content=True)[0]["body"], body)
        self.assertEqual(ticket.description, "Description text.")
        self.assertNotIn("Please retry", request_messages(ticket)[0]["body"])

    def test_full_content_html_only_mail_is_safe_plain_text(self):
        ticket = make_ticket()
        MailIntake.objects.create(dedupe_key="full-html", linked_ticket=ticket,
            from_email="sender@example.com", body_html="<p>Dear team,</p><p>Full message.</p><p>Regards,<br>Dimple</p><script>alert('bad')</script>",
            received_at=timezone.now())
        text = request_messages(ticket, full_content=True)[0]["body"]
        self.assertIn("Dear team,", text)
        self.assertIn("Dimple", text)
        self.assertNotIn("alert", text)
        self.assertNotIn("<p>", text)

    def test_manual_description_is_preserved_without_email(self):
        ticket = make_ticket(source="MANUAL", description="Hello,\n\nPlease add a user.\n\nRegards,\nDimple")
        self.assertEqual(request_messages(ticket, full_content=True)[0]["body"], ticket.description)

    def test_intake_description_field_is_opt_in(self):
        ticket = make_ticket()
        row = base_ticket_queryset().get(pk=ticket.pk)
        self.assertNotIn("description_messages", SupportTicketListSerializer(row).data)
        data = SupportTicketListSerializer(row, context={"include_description_messages": True}).data
        self.assertEqual(data["description_messages"][0]["body"], ticket.description)

    def test_full_threads_are_prefetched_for_the_whole_page(self):
        for index in range(3):
            ticket = make_ticket()
            for reply in (False, True):
                MailIntake.objects.create(dedupe_key=f"prefetch-{index}-{reply}", linked_ticket=ticket,
                    from_email="sender@example.com", body_text=f"Hello,\nMessage {index}.\nRegards,\nSender",
                    is_thread_reply=reply, received_at=timezone.now())
        tickets = list(with_description_messages(base_ticket_queryset()))
        with self.assertNumQueries(0):
            for ticket in tickets:
                messages = request_messages(ticket, full_content=True)
                self.assertEqual([message["kind"] for message in messages], ["ORIGINAL", "REPLY"])
                self.assertTrue(all("Regards," in message["body"] for message in messages))

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
