"""Route resolution tests.

These exist because `apps.masters.urls` registers a `teams/<unique_id>/` detail
route at the bare API root. Django resolves in declaration order, so if the
masters router is ever moved above the team routes, `teams/workload/` silently
becomes a UUID lookup and 404s. A unit test catches that; a manual click-through
might not.
"""

from django.test import SimpleTestCase
from django.urls import Resolver404, resolve


class TeamRouteResolutionTests(SimpleTestCase):
    def test_workload_resolves_to_the_workload_view(self):
        match = resolve("/api/v1/teams/workload/")
        self.assertEqual(match.url_name, "team-workload")

    def test_assignment_board_resolves(self):
        match = resolve("/api/v1/teams/assignment-board/")
        self.assertEqual(match.url_name, "team-assignment-board")

    def test_team_master_detail_still_resolves(self):
        """The fix must not break the masters route it shares a prefix with."""
        match = resolve("/api/v1/teams/2f1d4c0e-0000-4000-8000-000000000000/")
        self.assertEqual(match.url_name, "team-detail")

    def test_team_master_list_still_resolves(self):
        match = resolve("/api/v1/teams/")
        self.assertEqual(match.url_name, "team-list")


class PrefixedAppRouteTests(SimpleTestCase):
    """Every prefix-scoped app must win over the bare-root routers."""

    def test_prefixed_routes_resolve(self):
        cases = [
            ("/api/v1/dashboard/kpis/", "dashboard-kpis"),
            ("/api/v1/reports/daily/", "report-daily"),
            ("/api/v1/reports/export/bugs/", "report-export-bugs"),
            ("/api/v1/auth/login/", "auth-login"),
        ]
        for path, expected in cases:
            with self.subTest(path=path):
                self.assertEqual(resolve(path).url_name, expected)

    def test_bug_action_routes_resolve(self):
        bug_id = "2f1d4c0e-0000-4000-8000-000000000000"
        for action in ["assign", "status", "updates", "testing", "resolve", "close", "reopen"]:
            with self.subTest(action=action):
                match = resolve(f"/api/v1/bugs/{bug_id}/{action}/")
                self.assertTrue(match.url_name.startswith("bug-"), match.url_name)

    def test_attachment_download_route_resolves(self):
        match = resolve("/api/v1/bugs/attachments/2f1d4c0e-0000-4000-8000-000000000000/download/")
        self.assertEqual(match.url_name, "attachment-download")

    def test_media_root_is_not_routed(self):
        """Spec 31: attachments must only be reachable through the gated view."""
        with self.assertRaises(Resolver404):
            resolve("/media/bug_attachments/2026/09/BUG-1/file.png")
