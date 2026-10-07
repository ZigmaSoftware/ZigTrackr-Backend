"""Dashboard summary follows the unified ticket workflow, without duplicates."""
from django.test import TestCase
from rest_framework.test import APIClient
from apps.accounts.models import UserRole
from apps.accounts.tests.test_user_management import seed_rbac
from apps.bugs.constants import BugStatus
from apps.bugs.tests.factories import make_bug, make_masters, make_user
from apps.dashboard.selectors.kpi import dashboard_kpis
from apps.tickets.constants import TicketStatus, TicketType
from apps.tickets.tests.factories import make_ticket


class UnifiedDashboardKpiTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.roles = seed_rbac()
        cls.admin = make_user("dashboard_admin")
        cls.developer = make_user("dashboard_developer")
        cls.other = make_user("dashboard_other")
        UserRole.objects.create(user=cls.admin, role=cls.roles["ADMIN"])
        UserRole.objects.create(user=cls.developer, role=cls.roles["DEVELOPER"])
        cls.project, cls.module, cls.priority, cls.severity = make_masters()

    def ticket(self, status, kind=TicketType.SERVICE_REQUEST, **kwargs):
        return make_ticket(owner=self.developer, reporter=self.developer, ticket_type=kind,
                           needs_review=False, status=status, **kwargs)

    def bug(self, status):
        return make_bug(bug_no=f"DASH-{status}", reporter=self.developer, owner=self.developer,
                        project=self.project, module=self.module, priority=self.priority,
                        severity=self.severity, status=status)

    def test_verification_includes_service_access_and_bug_tickets(self):
        self.ticket(TicketStatus.TESTING)
        self.ticket(TicketStatus.TESTING, TicketType.ACCESS_REQUEST)
        self.ticket(TicketStatus.ASSIGNED, TicketType.BUG, bug=self.bug(BugStatus.TESTING))
        metrics = dashboard_kpis(self.admin)
        self.assertEqual(metrics["testing"], 3)
        self.assertEqual(metrics["assigned"], 0)
        self.assertEqual(metrics["total_open"], 3)

    def test_linked_bug_overrides_stale_status_and_is_not_double_counted(self):
        self.ticket(TicketStatus.IN_PROGRESS, TicketType.BUG, bug=self.bug(BugStatus.CLOSED))
        self.ticket(TicketStatus.CLOSED, TicketType.BUG, bug=self.bug(BugStatus.IN_PROGRESS))
        metrics = dashboard_kpis(self.admin)
        self.assertEqual(metrics["in_progress"], 1)
        self.assertEqual(metrics["total_open"], 1)

    def test_hold_and_reopen_include_non_bug_tickets(self):
        self.ticket(TicketStatus.ON_HOLD)
        self.ticket(TicketStatus.REOPENED, TicketType.ACCESS_REQUEST)
        metrics = dashboard_kpis(self.admin)
        self.assertEqual(metrics["on_hold"], 1)
        self.assertEqual(metrics["reopened"], 1)

    def test_legacy_orphan_bugs_remain_counted(self):
        self.bug(BugStatus.TESTING)
        self.ticket(TicketStatus.TESTING)
        self.assertEqual(dashboard_kpis(self.admin)["testing"], 2)

    def test_deleted_and_unrelated_tickets_do_not_leak_into_developer_summary(self):
        self.ticket(TicketStatus.TESTING)
        self.ticket(TicketStatus.TESTING, is_deleted=True)
        make_ticket(owner=self.other, reporter=self.other, needs_review=False,
                    ticket_type=TicketType.SERVICE_REQUEST, status=TicketStatus.TESTING)
        metrics = dashboard_kpis(self.developer)
        self.assertEqual(metrics["testing"], 1)
        self.assertEqual(metrics["total_open"], 1)

    def test_summary_endpoint_still_requires_dashboard_permission(self):
        client = APIClient()
        self.assertIn(client.get("/api/v1/dashboard/kpis/").status_code, (401, 403))
        client.force_authenticate(self.other)
        self.assertEqual(client.get("/api/v1/dashboard/kpis/").status_code, 403)
        client.force_authenticate(self.admin)
        response = client.get("/api/v1/dashboard/kpis/")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["data"]["testing"], 0)
