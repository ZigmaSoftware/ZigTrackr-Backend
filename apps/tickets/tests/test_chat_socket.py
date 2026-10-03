"""The public socket shares the persisted chat room and rejects strangers."""

from asgiref.sync import async_to_sync
from channels.testing import WebsocketCommunicator
from channels.db import database_sync_to_async
from django.core import signing
from django.test import TransactionTestCase, override_settings

from apps.bugs.tests.factories import make_user
from apps.tickets.constants import TicketStatus, TicketType
from apps.tickets.models import TicketChatMessage
from apps.tickets.services.chat_service import acknowledge_messages, change_message
from apps.tickets.services.public_track_service import COOKIE_NAME, TOKEN_SALT
from apps.tickets.tests.factories import make_ticket
from config.asgi import application

TEST_LAYER = {"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}}
SOCKET_PATH = "/api/v1/tickets/public/track/ws/"


@override_settings(CHANNEL_LAYERS=TEST_LAYER, ALLOWED_HOSTS=["localhost", "127.0.0.1"])
class PublicChatSocketTests(TransactionTestCase):
    def setUp(self):
        owner = make_user("socket_owner")
        self.ticket = make_ticket(
            ticket_type=TicketType.SERVICE_REQUEST, needs_review=False,
            ticket_no="TKT-2609-0022", owner=owner,
            status=TicketStatus.IN_PROGRESS,
            reported_by_email="sender@example.com",
        )

    def _headers(self, token=None):
        rows = [(b"origin", b"http://localhost:5173")]
        if token:
            rows.append((b"cookie", f"{COOKIE_NAME}={token}".encode()))
        return rows

    def test_no_signed_cookie_cannot_join(self):
        async def scenario():
            socket = WebsocketCommunicator(application, SOCKET_PATH, headers=self._headers())
            connected, _ = await socket.connect()
            self.assertFalse(connected)
            await socket.disconnect()

        async_to_sync(scenario)()

    def test_requester_send_is_saved_and_broadcast(self):
        token = signing.dumps(
            {"ticket_id": self.ticket.pk, "email": "sender@example.com"},
            salt=TOKEN_SALT,
        )

        async def scenario():
            socket = WebsocketCommunicator(
                application, SOCKET_PATH, headers=self._headers(token),
            )
            connected, _ = await socket.connect()
            self.assertTrue(connected)
            await socket.send_json_to({"type": "send", "message": "Is there an update?"})
            frame = await socket.receive_json_from(timeout=3)
            self.assertEqual(frame["type"], "message")
            self.assertEqual(frame["message"]["message_text"], "Is there an update?")
            await socket.disconnect()

        async_to_sync(scenario)()
        self.assertEqual(TicketChatMessage.objects.filter(ticket=self.ticket).count(), 1)

    def test_edit_broadcasts_change_notification(self):
        token = signing.dumps(
            {"ticket_id": self.ticket.pk, "email": "sender@example.com"},
            salt=TOKEN_SALT,
        )

        async def scenario():
            socket = WebsocketCommunicator(application, SOCKET_PATH, headers=self._headers(token))
            connected, _ = await socket.connect()
            self.assertTrue(connected)
            await socket.send_json_to({"type": "send", "message": "Before"})
            sent = await socket.receive_json_from(timeout=3)
            await database_sync_to_async(change_message)(
                ticket=self.ticket, message_id=sent["message"]["id"], action="edit",
                requester_email="sender@example.com", text="After",
            )
            changed = await socket.receive_json_from(timeout=3)
            self.assertEqual(changed["type"], "message_changed")
            self.assertEqual(changed["message"]["message_text"], "After")
            await socket.disconnect()

        async_to_sync(scenario)()

    def test_receipt_broadcasts_refresh_notification(self):
        staff = TicketChatMessage.objects.create(
            ticket=self.ticket, sender_type="STAFF", sender_user=self.ticket.owner,
            sender_display_name="Support", message_text="Update",
        )
        token = signing.dumps(
            {"ticket_id": self.ticket.pk, "email": "sender@example.com"}, salt=TOKEN_SALT,
        )

        async def scenario():
            socket = WebsocketCommunicator(application, SOCKET_PATH, headers=self._headers(token))
            connected, _ = await socket.connect()
            self.assertTrue(connected)
            await database_sync_to_async(acknowledge_messages)(
                ticket=self.ticket, message_ids=[staff.unique_id], status="read",
                requester_email="sender@example.com",
            )
            frame = await socket.receive_json_from(timeout=3)
            self.assertEqual(frame["type"], "receipts_changed")
            await socket.disconnect()

        async_to_sync(scenario)()
