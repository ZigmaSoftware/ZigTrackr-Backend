"""Chat edits and deletions are limited to the original author."""

from django.test import TestCase
from django.conf import settings
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken

from apps.bugs.tests.factories import make_user
from apps.tickets.constants import TicketStatus, TicketType
from apps.tickets.models import TicketChatMessage
from apps.tickets.services.chat_service import change_message
from common.exceptions.domain import WorkflowValidationError
from apps.tickets.tests.factories import make_ticket


class StaffChatActionTests(TestCase):
    def setUp(self):
        self.owner = make_user("chat_owner", is_superuser=True)
        self.other = make_user("chat_other", is_superuser=True)
        self.ticket = make_ticket(
            ticket_type=TicketType.SERVICE_REQUEST, needs_review=False,
            ticket_no="TKT-2609-0099", owner=self.owner,
            status=TicketStatus.IN_PROGRESS,
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.owner)
        self.base = f"/api/v1/tickets/{self.ticket.unique_id}/chat/messages/"

    def test_staff_can_edit_own_message_but_other_staff_cannot(self):
        sent = self.client.post(self.base, {"message": "Before"}, format="json")
        self.assertEqual(sent.status_code, 200, sent.content)
        action = f"{self.base}{sent.json()['data']['id']}/"
        self.client.force_authenticate(user=self.other)
        self.assertEqual(self.client.post(action, {"action": "edit", "message": "No"}, format="json").status_code, 403)
        self.assertEqual(self.client.post(action, {"action": "delete"}, format="json").status_code, 403)
        self.client.force_authenticate(user=self.owner)
        edited = self.client.post(action, {"action": "edit", "message": "After"}, format="json")
        self.assertEqual(edited.status_code, 200, edited.content)
        self.assertEqual(edited.json()["data"]["message_text"], "After")
        self.assertTrue(edited.json()["data"]["can_edit"])
        self.assertEqual(TicketChatMessage.objects.get(ticket=self.ticket).revisions.count(), 1)

    def test_message_from_another_ticket_cannot_be_changed(self):
        other_ticket = make_ticket(ticket_type=TicketType.SERVICE_REQUEST, needs_review=False,
                                   ticket_no="TKT-2609-0100", owner=self.owner,
                                   status=TicketStatus.IN_PROGRESS)
        row = TicketChatMessage.objects.create(ticket=other_ticket, sender_type="STAFF",
                                                sender_user=self.owner, sender_display_name="Owner",
                                                message_text="Other ticket")
        response = self.client.post(f"{self.base}{row.unique_id}/", {"action": "delete"}, format="json")
        self.assertEqual(response.status_code, 400)
        row.refresh_from_db()
        self.assertFalse(row.is_deleted)

    def test_malformed_ids_and_reactions_are_rejected(self):
        sent = self.client.post(self.base, {"message": "Hello"}, format="json")
        action = f"{self.base}{sent.json()['data']['id']}/"
        self.assertEqual(self.client.post(action, {"action": "react", "emoji": ["👍"]}, format="json").status_code, 400)
        self.assertEqual(self.client.post(f"{self.base}invalid-id/", {"action": "star"}, format="json").status_code, 400)
        self.assertEqual(self.client.post(self.base, {"message": "Reply", "reply_to": "invalid-id"}, format="json").status_code, 400)

    def test_star_is_personal_but_reaction_and_pin_are_shared(self):
        sent = self.client.post(self.base, {"message": "Shared"}, format="json").json()["data"]
        action = f"{self.base}{sent['id']}/"
        self.client.post(action, {"action": "star"}, format="json")
        self.client.post(action, {"action": "react", "emoji": "👍"}, format="json")
        self.client.post(action, {"action": "pin"}, format="json")

        self.client.force_authenticate(user=self.other)
        other_view = self.client.get(self.base).json()["data"][0]
        self.assertFalse(other_view["is_starred"])
        self.assertFalse(other_view["can_edit"])
        self.assertTrue(other_view["is_pinned"])
        self.assertEqual(other_view["reactions"], [{"emoji": "👍", "count": 1, "mine": False}])

    def test_staff_receipts_progress_and_lock_requester_edit(self):
        requester = TicketChatMessage.objects.create(
            ticket=self.ticket, sender_type="REQUESTER", sender_email="sender@example.com",
            sender_display_name="Sender", message_text="Please check",
        )
        path = f"/api/v1/tickets/{self.ticket.unique_id}/chat/receipts/"
        delivered = self.client.post(path, {
            "message_ids": [str(requester.unique_id)], "status": "delivered",
        }, format="json")
        self.assertEqual(delivered.status_code, 200, delivered.content)
        self.assertIsNotNone(delivered.json()["data"][0]["delivered_at"])
        self.assertIsNone(delivered.json()["data"][0]["read_at"])
        read = self.client.post(path, {
            "message_ids": [str(requester.unique_id)], "status": "read",
        }, format="json")
        self.assertEqual(read.status_code, 200, read.content)
        self.assertIsNotNone(read.json()["data"][0]["read_at"])
        with self.assertRaises(WorkflowValidationError):
            change_message(ticket=self.ticket, message_id=requester.unique_id,
                           action="edit", requester_email="sender@example.com", text="Changed")

    def test_staff_cannot_acknowledge_staff_messages(self):
        sent = self.client.post(self.base, {"message": "From staff"}, format="json").json()["data"]
        path = f"/api/v1/tickets/{self.ticket.unique_id}/chat/receipts/"
        response = self.client.post(path, {
            "message_ids": [sent["id"]], "status": "read",
        }, format="json")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["data"], [])
        self.assertIsNone(TicketChatMessage.objects.get(unique_id=sent["id"]).read_at)

    def test_cookie_authenticated_action_accepts_vite_fallback_origin(self):
        row = TicketChatMessage.objects.create(
            ticket=self.ticket, sender_type="STAFF", sender_user=self.owner,
            sender_display_name=self.owner.display_name, message_text="Original",
        )
        client = APIClient(enforce_csrf_checks=True)
        client.cookies[settings.AUTH_COOKIE_NAME] = str(AccessToken.for_user(self.owner))
        client.cookies["csrftoken"] = "a" * 32
        action = f"{self.base}{row.unique_id}/"
        headers = {"HTTP_ORIGIN": "http://localhost:5174", "HTTP_X_CSRFTOKEN": "a" * 32}

        accepted = client.post(action, {"action": "edit", "message": "Updated"}, format="json", **headers)
        self.assertEqual(accepted.status_code, 200, accepted.content)
        self.assertEqual(accepted.json()["data"]["message_text"], "Updated")

        rejected = client.post(action, {"action": "edit", "message": "Unsafe"}, format="json",
                               HTTP_ORIGIN="http://untrusted.example", HTTP_X_CSRFTOKEN="a" * 32)
        self.assertEqual(rejected.status_code, 403)
        row.refresh_from_db()
        self.assertEqual(row.message_text, "Updated")
