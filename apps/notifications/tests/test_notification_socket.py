"""The live delivery path for notifications.

notify() is only half the feature: if the frame never reaches the recipient's
channel, the toast and the badge never move without a refresh. These tests
pin the contract between publisher and consumer.
"""

from channels.db import database_sync_to_async
from channels.layers import get_channel_layer
from django.test import TransactionTestCase, override_settings

from apps.bugs.tests.factories import make_user
from apps.notifications.models import NotificationType

IN_MEMORY = {"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}}


@override_settings(CHANNEL_LAYERS=IN_MEMORY)
class NotificationBroadcastTests(TransactionTestCase):
    async def test_the_row_reaches_the_recipients_own_channel(self):
        user = await database_sync_to_async(make_user)("notif_socket")

        layer = get_channel_layer()
        channel = await layer.new_channel()
        await layer.group_add(f"user_inbox_{user.pk}", channel)

        from apps.notifications.services import notify

        await database_sync_to_async(notify)(
            recipients=[user],
            notification_type=NotificationType.TICKET_ASSIGNED,
            title="TKT-2610-7001 assigned to you",
            message="Printer not working",
        )

        event = await layer.receive(channel)
        self.assertEqual(event["type"], "inbox.notification")
        payload = event["notification"]
        # The payload must be complete enough to render a toast without a
        # follow-up fetch -- that is the whole point of carrying it.
        self.assertEqual(payload["title"], "TKT-2610-7001 assigned to you")
        self.assertEqual(payload["message"], "Printer not working")
        self.assertFalse(payload["is_read"])
        self.assertIn("id", payload)
        self.assertIn("is_urgent", payload)

    async def test_an_urgent_type_is_flagged_for_a_persistent_toast(self):
        user = await database_sync_to_async(make_user)("notif_urgent")
        layer = get_channel_layer()
        channel = await layer.new_channel()
        await layer.group_add(f"user_inbox_{user.pk}", channel)

        from apps.notifications.services import notify

        await database_sync_to_async(notify)(
            recipients=[user],
            notification_type=NotificationType.BUG_OVERDUE,
            title="BUG-2610-0012 is overdue",
        )
        event = await layer.receive(channel)
        self.assertTrue(event["notification"]["is_urgent"])

    async def test_nothing_is_sent_when_the_only_recipient_is_the_actor(self):
        user = await database_sync_to_async(make_user)("notif_self")
        layer = get_channel_layer()
        channel = await layer.new_channel()
        await layer.group_add(f"user_inbox_{user.pk}", channel)

        from apps.notifications.services import notify

        await database_sync_to_async(notify)(
            recipients=[user],
            notification_type=NotificationType.TICKET_ASSIGNED,
            title="self-assignment",
            actor=user,
        )
        # Nothing queued: assert by draining with a short timeout.
        import asyncio

        with self.assertRaises(asyncio.TimeoutError):
            await asyncio.wait_for(layer.receive(channel), timeout=0.4)
