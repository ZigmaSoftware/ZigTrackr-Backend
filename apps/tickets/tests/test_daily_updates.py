"""Historical categories, local-date boundaries, and scoped calendar counts."""

from datetime import datetime
from zoneinfo import ZoneInfo

from django.core.cache import cache
from django.test import TestCase
from rest_framework.test import APIClient

from apps.accounts.models import UserRole
from apps.bugs.models import BugUpdate
from apps.bugs.tests.factories import make_bug, make_masters, make_user
from apps.bugs.tests.test_api import seed_rbac
from apps.tickets.models import TicketActivity, TicketUpdate
from apps.tickets.tests.factories import make_ticket


class DailyUpdatesTests(TestCase):
    url = "/api/v1/tickets/daily-updates/"

    @classmethod
    def setUpTestData(cls):
        roles = seed_rbac()
        cls.admin = make_user("daily_admin")
        cls.dev = make_user("daily_dev")
        cls.other = make_user("daily_other")
        for user, role in ((cls.admin, "ADMIN"), (cls.dev, "DEVELOPER"), (cls.other, "DEVELOPER")):
            UserRole.objects.create(user=user, role=roles[role])

    def setUp(self):
        cache.clear()
        self.client = APIClient()
        self.client.force_authenticate(self.admin)

    def activity(self, ticket, event, day="2026-10-05", clock="12:00:00"):
        return TicketActivity.objects.create(
            ticket=ticket, event_type=event, title=event, description="Daily test activity",
            actor_user=self.admin, occurred_at=datetime.fromisoformat(f"{day}T{clock}").replace(tzinfo=ZoneInfo("Asia/Kolkata")),
        )

    def data(self, **params):
        response = self.client.get(self.url, {"from_date": "2026-10-05", **params})
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()["data"]

    def test_categories_follow_event_date_not_current_ticket_status(self):
        ticket = make_ticket(owner=self.dev, status="CLOSED", needs_review=False)
        self.activity(ticket, "TICKET_RECEIVED", "2026-10-04")
        self.activity(ticket, "TICKET_ASSIGNED")
        self.activity(ticket, "TICKET_RECTIFIED", "2026-10-06")
        self.activity(ticket, "TICKET_CLOSED", "2026-10-07")
        daily = self.data(category="assigned")
        self.assertEqual(daily["count"], 1)
        self.assertEqual(daily["results"][0]["category"], "assigned")
        self.assertEqual(daily["results"][0]["ticket_uuid"], str(ticket.unique_id))
        self.assertTrue(daily["results"][0]["owner_role"])
        self.assertEqual(self.data()["counts"]["closed"], 0)
        ranged = self.data(from_date="2026-10-04", to_date="2026-10-07")
        self.assertEqual(ranged["counts"], {"all": 4, "unassigned": 1, "assigned": 1, "rectified": 1, "closed": 1, "other": 0})

    def test_inclusive_local_date_boundaries(self):
        ticket = make_ticket()
        self.activity(ticket, "TICKET_ASSIGNED", "2026-10-04", "23:59:59")
        self.activity(ticket, "TICKET_ASSIGNED", clock="00:00:00")
        self.activity(ticket, "TICKET_ASSIGNED", clock="23:59:59")
        self.activity(ticket, "TICKET_ASSIGNED", "2026-10-06", "00:00:00")
        self.assertEqual(self.data()["count"], 2)
        days = self.client.get(self.url + "calendar/", {"month": "2026-10-01"}).json()["data"]["days"]
        self.assertEqual(days["2026-10-04"]["all"], 1)
        self.assertEqual(days["2026-10-05"]["all"], 2)
        self.assertEqual(days["2026-10-06"]["all"], 1)

    def test_calendar_and_rows_exclude_invisible_and_deleted_tickets(self):
        visible = make_ticket(owner=self.dev)
        hidden = make_ticket(owner=self.other)
        deleted = make_ticket(owner=self.dev, is_deleted=True)
        for ticket in (visible, hidden, deleted):
            self.activity(ticket, "TICKET_ASSIGNED")
        self.client.force_authenticate(self.dev)
        self.assertEqual(self.data()["count"], 1)
        response = self.client.get(self.url + "calendar/", {"month": "2026-10-01"})
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["data"]["days"]["2026-10-05"]["all"], 1)

    def test_manual_updates_are_included_system_updates_not_duplicated(self):
        ticket = make_ticket(owner=self.dev, title="Payroll problem")
        self.activity(ticket, "TICKET_ASSIGNED")
        manual = TicketUpdate.objects.create(ticket=ticket, update_text="Checking payroll", created_by_user=self.dev)
        automatic = TicketUpdate.objects.create(ticket=ticket, update_text="Assigned", source="SYSTEM")
        at = datetime(2026, 10, 5, 12, tzinfo=ZoneInfo("Asia/Kolkata"))
        TicketUpdate.objects.filter(pk__in=(manual.pk, automatic.pk)).update(created_at=at)
        self.assertEqual(self.data()["count"], 2)
        searched = self.data(search="Checking")
        self.assertEqual(searched["count"], 1)
        self.assertEqual(searched["counts"]["all"], 2)
        self.assertEqual(self.data(search="Payroll")["count"], 2)

    def test_bug_update_uses_status_and_owner_snapshot(self):
        project, _, priority, severity = make_masters()
        bug = make_bug(bug_no="BUG-DAILY-1", reporter=self.admin, project=project,
                       priority=priority, severity=severity, owner=self.other)
        ticket = make_ticket(bug=bug, owner=self.other)
        update = BugUpdate.objects.create(bug=bug, status="TESTING", owner=self.dev, update_text="Fix ready", updated_by=self.dev, update_date="2026-10-05")
        BugUpdate.objects.filter(pk=update.pk).update(created_at=datetime(2026, 10, 5, 12, tzinfo=ZoneInfo("Asia/Kolkata")))
        result = self.data(category="rectified")["results"][0]
        self.assertEqual(result["owner_name"], self.dev.display_name)
        self.assertEqual(result["ticket_uuid"], str(ticket.unique_id))

    def test_system_authored_requester_bug_reply_is_not_lost(self):
        project, _, priority, severity = make_masters()
        bug = make_bug(bug_no="BUG-DAILY-REPLY", reporter=self.admin, project=project,
                       priority=priority, severity=severity, owner=self.dev)
        make_ticket(bug=bug, owner=self.dev)
        at = datetime(2026, 10, 5, 12, tzinfo=ZoneInfo("Asia/Kolkata"))
        for remarks, text in (("Email reply from requester@example.test", "More details from requester"),
                              ("", "Status changed to testing")):
            update = BugUpdate.objects.create(bug=bug, status="IN_PROGRESS", owner=self.dev,
                                             update_text=text, updated_by=self.admin, update_date="2026-10-05",
                                             remarks=remarks, is_system_generated=True)
            BugUpdate.objects.filter(pk=update.pk).update(created_at=at)
        data = self.data()
        self.assertEqual(data["count"], 1)
        self.assertEqual(data["results"][0]["text"], "More details from requester")

    def test_linked_bug_scope_cannot_be_bypassed_through_ticket(self):
        project, _, priority, severity = make_masters()
        bug = make_bug(bug_no="BUG-DAILY-HIDDEN", reporter=self.other, project=project,
                       priority=priority, severity=severity, owner=self.other)
        ticket = make_ticket(bug=bug, owner=self.dev)
        self.activity(ticket, "TICKET_ASSIGNED")
        self.client.force_authenticate(self.dev)
        self.assertEqual(self.data()["count"], 0)
        response = self.client.get(self.url + "calendar/", {"month": "2026-10-01"})
        self.assertEqual(response.json()["data"]["days"], {})

    def test_pagination_and_empty_results(self):
        ticket = make_ticket()
        for _ in range(3):
            self.activity(ticket, "TICKET_RECEIVED")
        data = self.data(limit=2, page=2)
        self.assertEqual((data["count"], data["total_pages"], len(data["results"])), (3, 2, 1))
        self.assertEqual(self.data(from_date="2026-10-08")["count"], 0)

    def test_invalid_dates_and_filters_rejected(self):
        for params in ({"from_date": "wrong"}, {"from_date": "2026-10-06", "to_date": "2026-10-05"},
                       {"category": "bogus"}, {"limit": 1000}, {"month": "1800-01-01"}):
            self.assertEqual(self.client.get(self.url, params).status_code, 400)

    def test_authentication_and_permission_required_on_both_endpoints(self):
        for suffix in ("", "calendar/"):
            self.client.force_authenticate(user=None)
            self.assertIn(self.client.get(self.url + suffix).status_code, (401, 403))
            user = make_user(f"no_daily_permission_{len(suffix)}")
            self.client.force_authenticate(user)
            self.assertEqual(self.client.get(self.url + suffix).status_code, 403)
