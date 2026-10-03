"""The chat workspace inbox exposes only scoped ticket summaries."""

from django.test import TestCase
from rest_framework.test import APIClient

from apps.tickets.constants import TicketStatus, TicketType
from apps.tickets.models import TicketChatMessage
from apps.tickets.tests.factories import make_ticket, make_user
from apps.dashboard.selectors import sidebar_counts


class ChatInboxTests(TestCase):
    def setUp(self):
        self.owner = make_user("inbox_owner", is_superuser=True)
        self.other = make_user("inbox_other", is_superuser=True)
        self.active = make_ticket(
            owner=self.owner, ticket_type=TicketType.SERVICE_REQUEST,
            ticket_no="TKT-2609-0201", title="Printer problem",
            needs_review=False, status=TicketStatus.IN_PROGRESS,
        )
        self.closed = make_ticket(
            owner=self.other, ticket_type=TicketType.ACCESS_REQUEST,
            ticket_no="TKT-2609-0202", title="Access request",
            needs_review=False, status=TicketStatus.CLOSED,
        )
        self.unassigned = make_ticket(title="Not a conversation")
        self.message = TicketChatMessage.objects.create(
            ticket=self.active, sender_type="REQUESTER",
            sender_display_name="Requester", sender_email="sender@example.com",
            message_text="The printer is offline.",
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.owner)
        self.url = "/api/v1/tickets/chat/inbox/"

    def test_inbox_preview_unread_and_pagination(self):
        response = self.client.get(self.url, {"limit": 1})
        self.assertEqual(response.status_code, 200, response.content)
        page = response.json()["data"]
        self.assertEqual(page["count"], 2)
        self.assertEqual(len(page["results"]), 1)
        row = page["results"][0]
        self.assertEqual(row["id"], str(self.active.unique_id))
        self.assertEqual(row["last_message_text"], "The printer is offline.")
        self.assertEqual(row["unread_count"], 1)
        self.assertFalse(row["last_message_is_mine"])
        self.assertEqual(row["requester_email"], "sender@example.com")
        self.assertEqual(self.client.get(self.url, {"limit": 1, "page": 2}).json()["data"]["results"][0]["id"],
                         str(self.closed.unique_id))

    def test_filters_and_read_receipts(self):
        self.assertEqual(self.client.get(self.url, {"filter": "mine"}).json()["data"]["count"], 1)
        self.assertEqual(self.client.get(self.url, {"filter": "closed"}).json()["data"]["count"], 1)
        self.assertEqual(self.client.get(self.url, {"filter": "unread"}).json()["data"]["count"], 1)
        self.assertEqual(sidebar_counts(self.owner)["chat_unread"], 1)
        self.assertEqual(self.client.get(self.url, {"search": "printer"}).json()["data"]["count"], 1)
        self.assertEqual(self.client.get(self.url, {"search": "not a conversation"}).json()["data"]["count"], 0)
        self.client.post(f"/api/v1/tickets/{self.active.unique_id}/chat/receipts/", {
            "message_ids": [str(self.message.unique_id)], "status": "read",
        }, format="json")
        self.assertEqual(self.client.get(self.url, {"filter": "unread"}).json()["data"]["count"], 0)
        self.assertEqual(sidebar_counts(self.owner)["chat_unread"], 0)
        self.assertEqual(self.client.get(self.url, {"filter": "invalid"}).status_code, 400)

    def test_staff_preview_identifies_the_actual_sender(self):
        TicketChatMessage.objects.create(
            ticket=self.active, sender_type="STAFF", sender_user=self.owner,
            sender_display_name=self.owner.display_name, message_text="I will check it.",
        )
        own = self.client.get(self.url).json()["data"]["results"][0]
        self.assertTrue(own["last_message_is_mine"])
        self.assertEqual(own["last_message_sender_name"], self.owner.display_name)
        self.client.force_authenticate(user=self.other)
        someone_else = self.client.get(self.url).json()["data"]["results"][0]
        self.assertFalse(someone_else["last_message_is_mine"])

    def test_authentication_is_required(self):
        self.client.force_authenticate(user=None)
        self.assertEqual(self.client.get(self.url).status_code, 401)
