"""The per-user inbox socket is what makes unread counts live.

Without it, a message on a conversation the user does not have open was
invisible until a poll fired, so these tests pin the two properties that
matter: an unauthenticated connection is refused, and a message on an
unrelated ticket still reaches the user's own channel.
"""

import asyncio
from unittest.mock import AsyncMock, patch

from channels.db import database_sync_to_async
from channels.layers import get_channel_layer
from channels.testing import WebsocketCommunicator
from django.test import TransactionTestCase, override_settings

from apps.accounts.models import Role, UserRole
from apps.bugs.tests.factories import make_user
from apps.masters.models import CompanyMaster
from apps.tickets.consumers import UserInboxConsumer
from apps.tickets.constants import TicketStatus, TicketType
from apps.tickets.tests.factories import make_ticket
from config.asgi import application

PATH = "/api/v1/tickets/inbox/ws/"

# The real layer is Redis; tests must not depend on it being up.
IN_MEMORY = {"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}}


@override_settings(CHANNEL_LAYERS=IN_MEMORY)
class UserInboxSocketTests(TransactionTestCase):
    def setUp(self):
        self.user = make_user("inbox_watcher")

    async def test_anonymous_connection_is_refused(self):
        communicator = WebsocketCommunicator(application, PATH)
        connected, _ = await communicator.connect()
        # Refusal is the property under test. The close code is not asserted:
        # closing before accept() is reported as a plain rejection, so the
        # 4403 the consumer passes is not what surfaces here.
        self.assertFalse(connected)
        await communicator.disconnect()

    async def test_nudge_reaches_the_users_own_group(self):
        """Delivery is asserted at the group level.

        Driving a real signed-in socket here would mean minting a JWT and
        replaying the cookie, which tests the auth layer rather than the
        fan-out. The group name is the contract between publisher and
        consumer, so that is what is pinned.
        """
        layer = get_channel_layer()
        channel = await layer.new_channel()
        group = f"user_inbox_{self.user.pk}"
        await layer.group_add(group, channel)

        ticket = await database_sync_to_async(make_ticket)(
            ticket_type=TicketType.SERVICE_REQUEST, needs_review=False,
            ticket_no="TKT-2610-9001", owner=self.user,
            status=TicketStatus.IN_PROGRESS, reported_by_email="someone@example.com",
        )

        from apps.tickets.services.chat_service import notify_inbox_changed

        await database_sync_to_async(notify_inbox_changed)(ticket)

        event = await layer.receive(channel)
        self.assertEqual(event["type"], "inbox.changed")

    async def test_owner_is_nudged_without_holding_a_broad_codename(self):
        """The ticket's own owner must be reached even with no view_all."""
        layer = get_channel_layer()
        channel = await layer.new_channel()
        await layer.group_add(f"user_inbox_{self.user.pk}", channel)

        ticket = await database_sync_to_async(make_ticket)(
            ticket_type=TicketType.SERVICE_REQUEST, needs_review=False,
            ticket_no="TKT-2610-9002", owner=self.user,
            status=TicketStatus.IN_PROGRESS, reported_by_email="someone@example.com",
        )

        from apps.tickets.services.chat_service import notify_inbox_changed

        await database_sync_to_async(notify_inbox_changed)(ticket)
        self.assertEqual((await layer.receive(channel))["type"], "inbox.changed")

    async def test_permission_refresh_is_contentless_and_rechecks_authorization(self):
        with patch.object(UserInboxConsumer, "_authorize", new_callable=AsyncMock) as authorize:
            authorize.side_effect = [self.user.pk, self.user.pk, None]
            communicator = WebsocketCommunicator(UserInboxConsumer.as_asgi(), PATH)
            connected, _ = await communicator.connect()
            self.assertTrue(connected)
            layer = get_channel_layer()
            group = f"user_inbox_{self.user.pk}"
            await layer.group_send(group, {"type": "inbox.permissions_changed"})
            self.assertEqual(await communicator.receive_json_from(), {"type": "permissions_changed"})
            await layer.group_send(group, {"type": "inbox.permissions_changed"})
            event = await communicator.receive_output()
            self.assertEqual(event["type"], "websocket.close")
            self.assertEqual(event["code"], 4403)
            await communicator.disconnect()

    async def test_company_permission_refresh_does_not_notify_other_company(self):
        from apps.accounts.services.company_roles import publish_role_access_changed

        @database_sync_to_async
        def prepare_memberships():
            role = Role.objects.create(code="INBOX_ROLE", name="Inbox role")
            other_company = CompanyMaster.objects.create(code="INBOX_OTHER", name="Other company")
            other = make_user("other_inbox_watcher", company=other_company)
            for user in (self.user, other):
                UserRole.objects.create(user=user, role=role)
            return role.pk, other.pk

        role_id, other_id = await prepare_memberships()
        layer = get_channel_layer()
        own_channel, other_channel = await layer.new_channel(), await layer.new_channel()
        await layer.group_add(f"user_inbox_{self.user.pk}", own_channel)
        await layer.group_add(f"user_inbox_{other_id}", other_channel)
        await database_sync_to_async(publish_role_access_changed)(role_id, self.user.company_id)
        self.assertEqual(await layer.receive(own_channel), {"type": "inbox.permissions_changed"})
        with self.assertRaises(asyncio.TimeoutError):
            await asyncio.wait_for(layer.receive(other_channel), timeout=0.05)
