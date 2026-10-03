"""Bug list filter tests (spec 24).

Two behaviours guarded here, both found by using the running app:

1. The My Bugs and Assigned to Me presets send owner=me / reporter=me. The
   presets are static config and cannot embed a per-user UUID, so the sentinel
   must resolve server-side.
2. A filter value that cannot match anything must return an empty list, not a
   400. A stale bookmark should not break the screen.
"""

from django.core.cache import cache
from django.test import Client, TestCase

from apps.accounts.models import UserRole
from apps.bugs.constants import BugStatus
from apps.bugs.tests.factories import make_bug, make_masters, make_team, make_user
from apps.bugs.tests.test_api import PASSWORD, seed_rbac


class BugFilterTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.roles = seed_rbac()
        cls.team = make_team("T")
        cls.project, cls.module, cls.priority, cls.severity = make_masters()

        cls.admin = cls._user("admin", "ADMIN")
        cls.dev = cls._user("kiran", "DEVELOPER", team=cls.team)
        cls.other = cls._user("arun", "DEVELOPER", team=cls.team)

        common = dict(project=cls.project, module=cls.module,
                      priority=cls.priority, severity=cls.severity)
        # Owned by dev, reported by other.
        make_bug(bug_no="OWN-1", reporter=cls.other, owner=cls.dev,
                 status=BugStatus.IN_PROGRESS, **common)
        # Reported by dev, owned by other.
        make_bug(bug_no="REP-1", reporter=cls.dev, owner=cls.other,
                 status=BugStatus.IN_PROGRESS, **common)
        # Neither.
        make_bug(bug_no="NONE-1", reporter=cls.admin, owner=cls.admin,
                 status=BugStatus.IN_PROGRESS, **common)

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
        response = self.client.post(
            "/api/v1/auth/login/",
            data={"username": user.username, "password": PASSWORD},
            content_type="application/json")
        assert response.status_code == 200, response.content
        return self.client

    def _bug_numbers(self, query):
        response = self.client.get(f"/api/v1/bugs/?limit=50&{query}")
        self.assertEqual(response.status_code, 200, response.content)
        return {row["bug_no"] for row in response.json()["data"]["results"]}

    # ---- THE "ME" SENTINEL ----

    def test_owner_me_resolves_to_caller(self):
        self.login(self.dev)
        self.assertEqual(self._bug_numbers("owner=me"), {"OWN-1"})

    def test_reporter_me_resolves_to_caller(self):
        self.login(self.dev)
        self.assertEqual(self._bug_numbers("reporter=me"), {"REP-1"})

    def test_me_is_per_user(self):
        """The same URL means something different for each caller."""
        self.login(self.dev)
        mine = self._bug_numbers("owner=me")
        self.client = Client()
        cache.clear()
        self.login(self.other)
        theirs = self._bug_numbers("owner=me")
        self.assertEqual(mine, {"OWN-1"})
        self.assertEqual(theirs, {"REP-1"})

    def test_me_is_case_insensitive(self):
        self.login(self.dev)
        self.assertEqual(self._bug_numbers("owner=ME"), {"OWN-1"})

    def test_owner_accepts_a_real_uuid(self):
        self.login(self.admin)
        self.assertEqual(self._bug_numbers(f"owner={self.dev.unique_id}"), {"OWN-1"})

    # ---- MALFORMED VALUES MUST NOT 400 ----

    def test_malformed_user_filter_returns_empty_not_error(self):
        self.login(self.admin)
        self.assertEqual(self._bug_numbers("owner=not-a-uuid"), set())
        self.assertEqual(self._bug_numbers("reporter=not-a-uuid"), set())

    def test_malformed_master_filters_return_empty_not_error(self):
        self.login(self.admin)
        for field in ["project", "module", "submodule", "department", "site"]:
            with self.subTest(field=field):
                response = self.client.get(f"/api/v1/bugs/?limit=5&{field}=not-a-uuid")
                self.assertEqual(response.status_code, 200, f"{field} should not 400")
                self.assertEqual(response.json()["data"]["count"], 0)

    def test_valid_master_filter_still_works(self):
        self.login(self.admin)
        found = self._bug_numbers(f"project={self.project.unique_id}")
        self.assertEqual(found, {"OWN-1", "REP-1", "NONE-1"})

    def test_empty_filter_value_is_ignored(self):
        """An empty param must not silently empty the list."""
        self.login(self.admin)
        self.assertEqual(len(self._bug_numbers("project=")), 3)

    # ---- PRESET QUERIES FROM THE SIDEBAR ----

    def test_preset_queries_all_succeed(self):
        """Every sidebar preset, exactly as the frontend sends it."""
        self.login(self.dev)
        presets = [
            "",
            "reporter=me",
            "owner=me&exclude_status=CLOSED&exclude_status=REJECTED",
            "unassigned=true&exclude_status=CLOSED&exclude_status=REJECTED",
            "priority=CRITICAL&exclude_status=CLOSED&exclude_status=REJECTED",
            "is_overdue=true",
            "status=TESTING",
            "reopened=true",
            "status=CLOSED",
            "is_update_pending=true",
        ]
        for query in presets:
            with self.subTest(preset=query or "all"):
                response = self.client.get(f"/api/v1/bugs/?limit=25&{query}")
                self.assertEqual(response.status_code, 200, f"{query}: {response.content}")
