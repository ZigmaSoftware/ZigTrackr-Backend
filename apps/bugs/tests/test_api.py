"""Bug API tests: envelope, filters, scoping, workflow actions."""

import datetime

from django.core.cache import cache
from django.test import Client, TestCase

from apps.accounts.models import Permission, Role, RolePermission, UserRole
from apps.bugs.constants import BugStatus
from apps.bugs.models import Bug
from apps.bugs.tests.factories import make_bug, make_masters, make_team, make_user
from common.permissions.codenames import PERMISSION_CATALOG, ROLE_PERMISSIONS

PASSWORD = "Zigma@12345"


def seed_rbac():
    """Create permissions and roles from the catalog, as the seed commands do."""
    perms = {}
    for order, (codename, name, module, screen, screen_name, act) in enumerate(
            PERMISSION_CATALOG, start=1):
        perms[codename] = Permission.objects.create(
            codename=codename, name=name, module=module, screen_code=screen,
            screen_name=screen_name, action=act, sort_order=order * 10,
        )
    roles = {}
    for code, codenames in ROLE_PERMISSIONS.items():
        role = Role.objects.create(code=code, name=code.title(), is_system=True)
        RolePermission.objects.bulk_create([
            RolePermission(role=role, permission=perms[c]) for c in codenames if c in perms
        ])
        roles[code] = role
    return roles


class BugApiTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.roles = seed_rbac()
        cls.team = make_team("ERP Team")
        cls.project, cls.module, cls.priority, cls.severity = make_masters()

        cls.admin = cls._user("admin", "ADMIN")
        cls.lead = cls._user("lead", "TEAM_LEAD", team=cls.team)
        cls.dev = cls._user("kiran", "DEVELOPER", team=cls.team)
        cls.other_dev = cls._user("arun", "DEVELOPER", team=cls.team)
        cls.qa = cls._user("qa", "TESTER", team=cls.team)
        cls.reporter = cls._user("finance", "REPORTER")
        cls.manager = cls._user("manager", "MANAGEMENT")

    @classmethod
    def _user(cls, username, role_code, team=None):
        user = make_user(username, team=team)
        user.set_password(PASSWORD)
        user.save()
        UserRole.objects.create(user=user, role=cls.roles[role_code])
        return user

    def setUp(self):
        cache.clear()
        self.client = Client()

    def login(self, user):
        """Authenticate through the real login endpoint.

        force_login() sets a Django session, which this API deliberately does
        not accept -- authentication is cookie-JWT only (spec 46). Going
        through /auth/login/ is what a browser actually does.
        """
        response = self.client.post(
            "/api/v1/auth/login/",
            data={"username": user.username, "password": PASSWORD},
            content_type="application/json",
        )
        assert response.status_code == 200, response.content
        return self.client

    def _bug(self, bug_no, **kwargs):
        kwargs.setdefault("reporter", self.reporter)
        return make_bug(bug_no=bug_no, project=self.project, priority=self.priority,
                        severity=self.severity, module=self.module, **kwargs)


class BugListTests(BugApiTestCase):
    def test_list_returns_envelope_and_pagination(self):
        self._bug("BUG-2609-0001")
        self.login(self.admin)
        response = self.client.get("/api/v1/bugs/")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["success"])
        data = body["data"]
        # Pagination nests inside data (spec 43 + house envelope).
        for key in ("count", "page", "total_pages", "results"):
            self.assertIn(key, data)
        self.assertEqual(data["count"], 1)

    def test_list_includes_computed_fields(self):
        self._bug("BUG-2609-0002", status=BugStatus.IN_PROGRESS,
                  reported_date=datetime.date(2026, 9, 1),
                  expected_closure_date=datetime.date(2026, 9, 5))
        self.login(self.admin)
        row = self.client.get("/api/v1/bugs/").json()["data"]["results"][0]
        for key in ("age_days", "aging_band", "is_overdue", "overdue_days",
                    "is_update_pending", "days_since_update"):
            self.assertIn(key, row)

    def test_filter_by_status(self):
        self._bug("B1", status=BugStatus.NEW)
        self._bug("B2", status=BugStatus.IN_PROGRESS)
        self.login(self.admin)
        results = self.client.get("/api/v1/bugs/?status=IN_PROGRESS").json()["data"]["results"]
        self.assertEqual([r["bug_no"] for r in results], ["B2"])

    def test_filter_by_overdue(self):
        self._bug("LATE", status=BugStatus.IN_PROGRESS,
                  expected_closure_date=datetime.date(2020, 1, 1))
        self._bug("FINE", status=BugStatus.IN_PROGRESS,
                  expected_closure_date=datetime.date(2099, 1, 1))
        self.login(self.admin)
        results = self.client.get("/api/v1/bugs/?is_overdue=true").json()["data"]["results"]
        self.assertEqual([r["bug_no"] for r in results], ["LATE"])

    def test_filter_unassigned(self):
        self._bug("NOONE", owner=None)
        self._bug("OWNED", owner=self.dev)
        self.login(self.admin)
        results = self.client.get("/api/v1/bugs/?unassigned=true").json()["data"]["results"]
        self.assertEqual([r["bug_no"] for r in results], ["NOONE"])

    def test_search_matches_bug_no_and_title(self):
        self._bug("BUG-2609-0042", title="Invoice printing fails")
        self._bug("BUG-2609-0043", title="Login slow")
        self.login(self.admin)
        by_no = self.client.get("/api/v1/bugs/?search=0042").json()["data"]["results"]
        self.assertEqual([r["bug_no"] for r in by_no], ["BUG-2609-0042"])
        by_title = self.client.get("/api/v1/bugs/?search=invoice").json()["data"]["results"]
        self.assertEqual([r["bug_no"] for r in by_title], ["BUG-2609-0042"])

    def test_ordering_by_age(self):
        self._bug("OLD", reported_date=datetime.date(2020, 1, 1))
        self._bug("NEW", reported_date=datetime.date(2026, 9, 14))
        self.login(self.admin)
        results = self.client.get("/api/v1/bugs/?ordering=-age_days").json()["data"]["results"]
        self.assertEqual([r["bug_no"] for r in results], ["OLD", "NEW"])

    def test_list_query_count_is_bounded(self):
        """Spec 47: no N+1. A page of bugs must not scale queries with rows."""
        for i in range(20):
            self._bug(f"Q{i:03d}", owner=self.dev)
        self.login(self.admin)
        # 4 = auth user lookup, permission resolution, pagination count, page.
        # This number must not grow with the row count; select_related covers
        # all 11 FK relations the list serializer touches.
        with self.assertNumQueries(4):
            self.client.get("/api/v1/bugs/?limit=20")

    def test_query_count_does_not_grow_with_rows(self):
        """The assertion that actually catches an N+1 regression."""
        for i in range(60):
            self._bug(f"R{i:03d}", owner=self.dev)
        self.login(self.admin)
        with self.assertNumQueries(4):
            self.client.get("/api/v1/bugs/?limit=60")


class BugScopingTests(BugApiTestCase):
    """Spec 14: row-level visibility per role."""

    def setUp(self):
        super().setUp()
        self.own = self._bug("OWN", owner=self.dev)
        self.teammate = self._bug("TEAMMATE", owner=self.other_dev)
        self.outsider = self._bug("OUTSIDER", owner=self.admin, reporter=self.admin)
        self.testing = self._bug("TESTING", owner=self.other_dev, status=BugStatus.TESTING)

    def _visible(self, user):
        self.login(user)
        results = self.client.get("/api/v1/bugs/?limit=100").json()["data"]["results"]
        return {r["bug_no"] for r in results}

    def test_admin_sees_everything(self):
        self.assertEqual(
            self._visible(self.admin),
            {"OWN", "TEAMMATE", "OUTSIDER", "TESTING"},
        )

    def test_management_sees_everything_readonly(self):
        self.assertEqual(
            self._visible(self.manager),
            {"OWN", "TEAMMATE", "OUTSIDER", "TESTING"},
        )

    def test_developer_sees_only_their_own(self):
        visible = self._visible(self.dev)
        self.assertIn("OWN", visible)
        self.assertNotIn("TEAMMATE", visible)
        self.assertNotIn("OUTSIDER", visible)

    def test_team_lead_sees_the_team(self):
        visible = self._visible(self.lead)
        self.assertIn("OWN", visible)
        self.assertIn("TEAMMATE", visible)
        self.assertNotIn("OUTSIDER", visible)

    def test_qa_sees_the_testing_queue(self):
        """Spec 14: QA views bugs waiting for testing regardless of ownership."""
        visible = self._visible(self.qa)
        self.assertIn("TESTING", visible)

    def test_invisible_bug_returns_404_not_403(self):
        """Do not confirm the existence of a bug the caller may not see."""
        self.login(self.dev)
        response = self.client.get(f"/api/v1/bugs/{self.outsider.unique_id}/")
        self.assertEqual(response.status_code, 404)

    def test_management_cannot_create(self):
        self.login(self.manager)
        response = self.client.post("/api/v1/bugs/", data={
            "project": str(self.project.unique_id), "title": "X", "description": "Y",
            "priority": str(self.priority.unique_id), "severity": str(self.severity.unique_id),
        }, content_type="application/json")
        self.assertEqual(response.status_code, 403)


class BugWorkflowApiTests(BugApiTestCase):
    def test_create_generates_bug_number(self):
        self.login(self.reporter)
        response = self.client.post("/api/v1/bugs/", data={
            "project": str(self.project.unique_id),
            "module": str(self.module.unique_id),
            "title": "Customer approval not loading",
            "description": "Approval page gives 500 error",
            "priority": str(self.priority.unique_id),
            "severity": str(self.severity.unique_id),
            "environment": "PRODUCTION",
        }, content_type="application/json")
        self.assertEqual(response.status_code, 201, response.content)
        data = response.json()["data"]
        self.assertRegex(data["bug_no"], r"^BUG-\d{4}-\d{4}$")
        self.assertEqual(data["status"], BugStatus.NEW)

    def test_create_rejects_module_from_another_project(self):
        from apps.masters.models import ModuleMaster, ProjectMaster

        other_project = ProjectMaster.objects.create(code="OTH", name="Other")
        foreign_module = ModuleMaster.objects.create(project=other_project, name="Foreign")
        self.login(self.reporter)
        response = self.client.post("/api/v1/bugs/", data={
            "project": str(self.project.unique_id),
            "module": str(foreign_module.unique_id),
            "title": "X", "description": "Y",
            "priority": str(self.priority.unique_id),
            "severity": str(self.severity.unique_id),
        }, content_type="application/json")
        self.assertEqual(response.status_code, 400)
        self.assertIn("module", response.json()["errors"])

    def test_assign_moves_new_to_assigned(self):
        bug = self._bug("A1")
        self.login(self.lead)
        response = self.client.post(
            f"/api/v1/bugs/{bug.unique_id}/assign/",
            data={"owner": str(self.dev.unique_id), "remarks": "Please analyse"},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["data"]["status"], BugStatus.ASSIGNED)

    def test_illegal_status_change_returns_409(self):
        bug = self._bug("A2")
        self.login(self.lead)
        response = self.client.post(
            f"/api/v1/bugs/{bug.unique_id}/status/",
            data={"status": BugStatus.CLOSED}, content_type="application/json",
        )
        self.assertEqual(response.status_code, 409)
        body = response.json()
        self.assertFalse(body["success"])
        self.assertIn("allowed_transitions", body["errors"])

    def test_close_without_required_fields_returns_400_with_all_fields(self):
        bug = self._bug("A3", status=BugStatus.RESOLVED, owner=self.dev)
        self.login(self.lead)
        response = self.client.post(
            f"/api/v1/bugs/{bug.unique_id}/status/",
            data={"status": BugStatus.CLOSED}, content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
        errors = response.json()["errors"]
        self.assertEqual(
            set(errors),
            {"root_cause", "resolution", "verification_result",
             "closure_remarks", "closed_date", "closed_by"},
        )

    def test_daily_update_clears_pending_flag(self):
        bug = self._bug("A4", status=BugStatus.IN_PROGRESS, owner=self.dev)
        self.login(self.dev)
        response = self.client.post(
            f"/api/v1/bugs/{bug.unique_id}/updates/",
            data={"update_text": "Analysed the API", "next_action": "Fix serializer"},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 201, response.content)
        detail = self.client.get(f"/api/v1/bugs/{bug.unique_id}/").json()["data"]
        self.assertFalse(detail["is_update_pending"])

    def test_detail_exposes_allowed_transitions(self):
        bug = self._bug("A5", status=BugStatus.NEW)
        self.login(self.admin)
        data = self.client.get(f"/api/v1/bugs/{bug.unique_id}/").json()["data"]
        values = {t["value"] for t in data["allowed_transitions"]}
        self.assertEqual(values, {BugStatus.ASSIGNED, BugStatus.REJECTED})

    def test_bugs_cannot_be_deleted(self):
        bug = self._bug("A6")
        self.login(self.admin)
        response = self.client.delete(f"/api/v1/bugs/{bug.unique_id}/")
        self.assertEqual(response.status_code, 405)
        self.assertTrue(Bug.objects.filter(pk=bug.pk).exists())

    def test_timeline_returns_events(self):
        from apps.bugs.services import assign_bug, create_bug

        bug = create_bug(actor=self.reporter, project=self.project, priority=self.priority,
                         severity=self.severity, title="T", description="D")
        assign_bug(bug=bug, new_owner=self.dev, actor=self.lead)
        self.login(self.admin)
        events = self.client.get(f"/api/v1/bugs/{bug.unique_id}/timeline/").json()["data"]
        types = [e["type"] for e in events]
        self.assertIn("STATUS", types)
        self.assertIn("ASSIGNMENT", types)
        # Chronological order (spec 28).
        timestamps = [e["timestamp"] for e in events]
        self.assertEqual(timestamps, sorted(timestamps))
