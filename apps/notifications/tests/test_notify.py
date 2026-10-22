"""The notification write path.

Until now nothing created a Notification row, so these tests pin the
properties that make the new `notify()` helper safe to call from inside a
service: it never notifies the actor, never duplicates, never survives a
rollback, and never raises.
"""

from unittest import mock

from django.db import transaction
from django.test import TestCase, TransactionTestCase, override_settings

from apps.bugs.tests.factories import make_user
from apps.notifications.models import Notification, NotificationType
from apps.notifications.services import notify
from apps.tickets.constants import TicketStatus, TicketType
from apps.tickets.tests.factories import make_ticket

IN_MEMORY = {"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}}


@override_settings(CHANNEL_LAYERS=IN_MEMORY)
class NotifyServiceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.actor = make_user("notify_actor")
        cls.owner = make_user("notify_owner")
        cls.other = make_user("notify_other")

    def test_actor_is_never_notified_of_their_own_action(self):
        notify(
            recipients=[self.actor, self.owner],
            notification_type=NotificationType.TICKET_ASSIGNED,
            title="Assigned", actor=self.actor,
        )
        recipients = set(Notification.objects.values_list("recipient_id", flat=True))
        self.assertEqual(recipients, {self.owner.pk})

    def test_recipients_are_deduplicated(self):
        """Someone often qualifies by two routes -- owner AND team lead."""
        notify(
            recipients=[self.owner, self.owner.pk, self.owner],
            notification_type=NotificationType.TICKET_ASSIGNED,
            title="Assigned",
        )
        self.assertEqual(Notification.objects.count(), 1)

    def test_accepts_users_or_bare_primary_keys(self):
        notify(
            recipients=[self.owner, self.other.pk],
            notification_type=NotificationType.TICKET_ASSIGNED,
            title="Assigned",
        )
        self.assertEqual(
            set(Notification.objects.values_list("recipient_id", flat=True)),
            {self.owner.pk, self.other.pk},
        )

    def test_none_and_empty_recipients_are_a_no_op(self):
        self.assertEqual(notify(
            recipients=[None], notification_type=NotificationType.TICKET_ASSIGNED,
            title="Assigned",
        ), [])
        self.assertEqual(notify(
            recipients=[], notification_type=NotificationType.TICKET_ASSIGNED,
            title="Assigned",
        ), [])
        self.assertEqual(Notification.objects.count(), 0)

    def test_notifying_only_the_actor_creates_nothing(self):
        notify(
            recipients=[self.actor],
            notification_type=NotificationType.TICKET_ASSIGNED,
            title="Assigned", actor=self.actor,
        )
        self.assertEqual(Notification.objects.count(), 0)

    def test_a_broadcast_failure_still_commits_the_row(self):
        """Telling someone is less important than the thing itself."""
        with mock.patch(
            "apps.notifications.services.notify.get_channel_layer",
            side_effect=RuntimeError("redis down"),
        ):
            notify(
                recipients=[self.owner],
                notification_type=NotificationType.TICKET_ASSIGNED,
                title="Assigned",
            )
        self.assertEqual(Notification.objects.count(), 1)

    def test_long_title_and_link_are_truncated_not_rejected(self):
        notify(
            recipients=[self.owner],
            notification_type=NotificationType.TICKET_ASSIGNED,
            title="x" * 400, link="/l" + "o" * 400,
        )
        row = Notification.objects.get()
        self.assertEqual(len(row.title), 200)
        self.assertEqual(len(row.link), 300)


@override_settings(CHANNEL_LAYERS=IN_MEMORY)
class NotifyRollbackTests(TransactionTestCase):
    """on_commit semantics need a real transaction, hence TransactionTestCase."""

    def test_a_rolled_back_operation_leaves_no_notification(self):
        owner = make_user("rollback_owner")
        try:
            with transaction.atomic():
                notify(
                    recipients=[owner],
                    notification_type=NotificationType.TICKET_ASSIGNED,
                    title="Assigned",
                )
                raise RuntimeError("business failure")
        except RuntimeError:
            pass
        self.assertEqual(Notification.objects.count(), 0)


@override_settings(CHANNEL_LAYERS=IN_MEMORY)
class AssignmentNotificationTests(TestCase):
    """The headline case: a developer is told work landed on them."""

    @classmethod
    def setUpTestData(cls):
        cls.lead = make_user("assign_lead")
        cls.dev = make_user("assign_dev")

    def test_assigning_a_ticket_notifies_exactly_the_new_owner(self):
        from apps.tickets.services.assignment_service import reassign_ticket

        ticket = make_ticket(
            ticket_type=TicketType.SERVICE_REQUEST, needs_review=False,
            ticket_no="TKT-2610-5001", owner=self.lead,
            status=TicketStatus.IN_PROGRESS, reported_by_email="r@example.com",
        )
        reassign_ticket(
            ticket=ticket, new_owner=self.dev, actor=self.lead, reason="over to you",
        )
        rows = list(Notification.objects.filter(
            notification_type=NotificationType.TICKET_ASSIGNED,
        ))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].recipient_id, self.dev.pk)
        self.assertEqual(rows[0].ticket_id, ticket.pk)
        self.assertIn(ticket.reference, rows[0].title)
        self.assertFalse(rows[0].is_read)

    def test_a_bug_ticket_notifies_once_not_twice(self):
        """reassign_ticket delegates to assign_bug for BUG tickets.

        Both layers notify, so without a guard the owner gets two alerts for
        one action -- the second pointing at the thinner ticket view.
        """
        from apps.bugs.tests.factories import make_bug, make_masters
        from apps.tickets.services.assignment_service import reassign_ticket

        project, module, priority, severity = make_masters()
        bug = make_bug(
            bug_no="BUG-2610-5002", reporter=self.lead, owner=self.lead,
            project=project, module=module, priority=priority, severity=severity,
        )
        ticket = make_ticket(
            ticket_type=TicketType.BUG, needs_review=False,
            ticket_no="TKT-2610-5002", owner=self.lead, bug=bug,
            status=TicketStatus.IN_PROGRESS, reported_by_email="r@example.com",
        )
        reassign_ticket(
            ticket=ticket, new_owner=self.dev, actor=self.lead, reason="over to you",
        )
        rows = Notification.objects.filter(recipient=self.dev)
        self.assertEqual(rows.count(), 1, list(rows.values_list("title", flat=True)))
        # The surviving one links to the bug, which is the richer view.
        self.assertEqual(rows.first().notification_type, NotificationType.BUG_ASSIGNED)
