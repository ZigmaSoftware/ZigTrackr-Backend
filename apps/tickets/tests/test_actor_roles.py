"""Staff role labels in ticket chat and activity responses."""

from django.test import TestCase
from rest_framework.test import APIClient

from apps.accounts.models import Role, UserRole
from apps.bugs.tests.factories import make_bug, make_masters, make_user
from apps.bugs.models import BugStatusHistory
from apps.tickets.constants import TicketStatus, TicketType
from apps.tickets.models import TicketChatMessage
from apps.tickets.services.activity_service import record_activity
from apps.tickets.tests.factories import make_ticket


class TicketActorRoleTests(TestCase):
    def setUp(self):
        self.admin = make_user("role_view_admin", is_superuser=True)
        self.author = make_user("dinesh")
        for code, name, rank, active in (
            ("ADMIN", "Admin", 10, True),
            ("DEVELOPER", "Developer", 30, True),
            ("TESTER", "Tester", 40, False),
        ):
            role = Role.objects.create(code=code, name=name, rank=rank)
            UserRole.objects.create(user=self.author, role=role, is_active=active)
        self.ticket = make_ticket(
            ticket_type=TicketType.SERVICE_REQUEST, needs_review=False,
            status=TicketStatus.IN_PROGRESS, owner=self.admin,
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.admin)

    def test_chat_messages_quotes_and_inbox_include_only_active_staff_roles(self):
        first = TicketChatMessage.objects.create(
            ticket=self.ticket, sender_type="STAFF", sender_user=self.author,
            sender_display_name="Dinesh", message_text="I will check.",
        )
        requester = TicketChatMessage.objects.create(
            ticket=self.ticket, sender_type="REQUESTER", sender_email="sender@example.com",
            sender_display_name="Requester", message_text="Thank you.",
        )
        TicketChatMessage.objects.create(
            ticket=self.ticket, sender_type="STAFF", sender_user=self.author,
            sender_display_name="Dinesh", message_text="Found it.", reply_to_message=first,
        )

        response = self.client.get(f"/api/v1/tickets/{self.ticket.unique_id}/chat/messages/")
        self.assertEqual(response.status_code, 200, response.content)
        messages = response.json()["data"]
        self.assertEqual(messages[0]["sender_role"], "Admin, Developer")
        self.assertEqual(messages[1]["id"], str(requester.unique_id))
        self.assertEqual(messages[1]["sender_role"], "")
        self.assertEqual(messages[2]["reply_to"]["sender_role"], "Admin, Developer")

        inbox = self.client.get("/api/v1/tickets/chat/inbox/")
        self.assertEqual(inbox.status_code, 200, inbox.content)
        row = next(row for row in inbox.json()["data"]["results"]
                   if row["id"] == str(self.ticket.unique_id))
        self.assertEqual(row["last_message_sender_role"], "Admin, Developer")

    def test_activity_timeline_labels_staff_but_not_requester_or_system(self):
        record_activity(
            ticket=self.ticket, event_type="STAFF_NOTE", title="Staff note", actor=self.author,
        )
        record_activity(
            ticket=self.ticket, event_type="REQUESTER_REPLY", title="Reply",
            actor_email="sender@example.com",
        )
        record_activity(ticket=self.ticket, event_type="SYSTEM_EVENT", title="System event")

        response = self.client.get(f"/api/v1/tickets/{self.ticket.unique_id}/timeline/")
        self.assertEqual(response.status_code, 200, response.content)
        events = {row["type"]: row for row in response.json()["data"]}
        self.assertEqual(events["STAFF_NOTE"]["actor_role"], "Admin, Developer")
        self.assertEqual(events["REQUESTER_REPLY"]["actor_role"], "")
        self.assertEqual(events["SYSTEM_EVENT"]["actor_role"], "")

    def test_legacy_bug_timeline_uses_history_actor_role(self):
        project, module, priority, severity = make_masters()
        bug = make_bug(
            bug_no="BUG-ROLE-1", reporter=self.author, project=project,
            module=module, priority=priority, severity=severity,
            status=TicketStatus.ASSIGNED, owner=self.admin,
        )
        self.ticket.bug = bug
        self.ticket.ticket_type = TicketType.BUG
        self.ticket.save(update_fields=["bug", "ticket_type"])
        BugStatusHistory.objects.create(
            bug=bug, from_status="NEW", to_status="ASSIGNED", changed_by=self.author,
        )

        response = self.client.get(f"/api/v1/tickets/{self.ticket.unique_id}/timeline/")
        self.assertEqual(response.status_code, 200, response.content)
        event = next(row for row in response.json()["data"] if row["type"] == "STATUS")
        self.assertEqual(event["actor_role"], "Admin, Developer")
