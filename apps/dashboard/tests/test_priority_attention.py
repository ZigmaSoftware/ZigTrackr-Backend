"""Regression test for the Priority Attention panel (spec 21.3).

Found during an end-to-end QA pass logged in as each role: rejecting a bug
left it permanently visible under both "Critical" and "Unassigned" on the
dashboard, because the view excluded only CLOSED, not the full set of
terminal statuses (spec 29 makes REJECTED terminal too). A rejected bug is
done -- it must never reappear in the "needs attention" panel.
"""

from django.core.cache import cache
from django.test import Client, TestCase

from apps.accounts.models import UserRole
from apps.bugs.constants import BugStatus
from apps.bugs.tests.factories import make_bug, make_masters, make_user
from apps.bugs.tests.test_api import PASSWORD, seed_rbac
from apps.masters.models import PriorityMaster


class PriorityAttentionTerminalStatusTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.roles = seed_rbac()
        cls.project, cls.module, _priority, cls.severity = make_masters()
        cls.critical_priority = PriorityMaster.objects.create(
            code="CRITICAL", name="Critical", rank=1,
        )
        cls.admin = make_user("pa_admin")
        cls.admin.set_password(PASSWORD)
        cls.admin.save()
        UserRole.objects.create(user=cls.admin, role=cls.roles["ADMIN"])

    def setUp(self):
        cache.clear()
        self.client = Client()
        self.client.post(
            "/api/v1/auth/login/",
            data={"username": "pa_admin", "password": PASSWORD},
            content_type="application/json",
        )

    def _bug(self, bug_no, **kwargs):
        return make_bug(bug_no=bug_no, reporter=self.admin, project=self.project,
                        priority=self.critical_priority, severity=self.severity, **kwargs)

    def test_rejected_bug_is_excluded_from_unassigned_panel(self):
        self._bug("REJ-1", status=BugStatus.REJECTED, owner=None)
        response = self.client.get("/api/v1/dashboard/priority-attention/")
        numbers = [b["bug_no"] for b in response.json()["data"]["unassigned"]]
        self.assertNotIn("REJ-1", numbers)

    def test_rejected_bug_is_excluded_from_critical_panel(self):
        self._bug("REJ-2", status=BugStatus.REJECTED, owner=None)
        response = self.client.get("/api/v1/dashboard/priority-attention/")
        numbers = [b["bug_no"] for b in response.json()["data"]["critical"]]
        self.assertNotIn("REJ-2", numbers)

    def test_closed_bug_is_excluded_from_both_panels(self):
        """The original behaviour this must not regress."""
        self._bug("CLO-1", status=BugStatus.CLOSED, owner=None)
        response = self.client.get("/api/v1/dashboard/priority-attention/")
        data = response.json()["data"]
        self.assertNotIn("CLO-1", [b["bug_no"] for b in data["unassigned"]])
        self.assertNotIn("CLO-1", [b["bug_no"] for b in data["critical"]])

    def test_genuinely_unassigned_open_bug_still_appears(self):
        """The fix must not over-correct into hiding real work."""
        self._bug("OPEN-1", status=BugStatus.NEW, owner=None)
        response = self.client.get("/api/v1/dashboard/priority-attention/")
        numbers = [b["bug_no"] for b in response.json()["data"]["unassigned"]]
        self.assertIn("OPEN-1", numbers)

    def test_genuinely_critical_open_bug_still_appears(self):
        self._bug("CRIT-1", status=BugStatus.IN_PROGRESS, owner=self.admin)
        response = self.client.get("/api/v1/dashboard/priority-attention/")
        numbers = [b["bug_no"] for b in response.json()["data"]["critical"]]
        self.assertIn("CRIT-1", numbers)
