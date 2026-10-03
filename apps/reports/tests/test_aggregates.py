"""Aggregation tests for the dashboard and reports.

These guard a specific, quiet failure: Bug.Meta sets ordering = ["-id"], and
Django appends ordering columns to the GROUP BY. A values().annotate() over a
default-ordered queryset therefore groups by (field, id) and returns one row
per bug, so every chart and report silently reports counts of 1. It looks
plausible on screen, which is exactly what makes it dangerous.
"""

import datetime

from django.test import TestCase

from apps.accounts.models import UserRole
from apps.bugs.constants import BugStatus
from apps.bugs.tests.factories import make_bug, make_masters, make_team, make_user
from apps.bugs.tests.test_api import seed_rbac
from apps.dashboard.selectors import (
    bugs_by_priority, bugs_by_status, dashboard_kpis, team_workload,
)
from apps.reports.selectors import aging_report, employee_report, project_report

TODAY = datetime.date(2026, 9, 14)


class AggregateGroupingTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.roles = seed_rbac()
        cls.team = make_team("T")
        cls.project, cls.module, cls.priority, cls.severity = make_masters()

        cls.admin = make_user("admin", team=cls.team)
        UserRole.objects.create(user=cls.admin, role=cls.roles["ADMIN"])
        # A Team Lead exercises the scoping branch that applies .distinct(),
        # which is where the grouping bug actually surfaced.
        cls.lead = make_user("lead", team=cls.team)
        UserRole.objects.create(user=cls.lead, role=cls.roles["TEAM_LEAD"])
        cls.dev = make_user("dev", team=cls.team)
        UserRole.objects.create(user=cls.dev, role=cls.roles["DEVELOPER"])

        # Six bugs, all IN_PROGRESS, all the same age band and priority.
        # Correct grouping collapses them into one row of count 6.
        for index in range(6):
            make_bug(
                bug_no=f"AGG-{index:03d}", reporter=cls.lead, project=cls.project,
                module=cls.module, priority=cls.priority, severity=cls.severity,
                status=BugStatus.IN_PROGRESS, owner=cls.dev,
                reported_date=TODAY - datetime.timedelta(days=20),
            )

    def test_bugs_by_status_groups(self):
        rows = {r["code"]: r["count"] for r in bugs_by_status(self.admin, TODAY)}
        self.assertEqual(rows[BugStatus.IN_PROGRESS], 6)

    def test_bugs_by_status_groups_for_scoped_user(self):
        """The distinct() branch must group identically."""
        rows = {r["code"]: r["count"] for r in bugs_by_status(self.lead, TODAY)}
        self.assertEqual(rows[BugStatus.IN_PROGRESS], 6)

    def test_bugs_by_priority_groups(self):
        rows = bugs_by_priority(self.admin, TODAY)
        matching = [r for r in rows if r["count"] > 0]
        self.assertEqual(len(matching), 1, "All six share one priority")
        self.assertEqual(matching[0]["count"], 6)

    def test_aging_report_groups(self):
        report = aging_report(self.admin, TODAY)
        bands = {r["code"]: r["count"] for r in report["summary"]}
        # 20 days old -> Critical Aging (spec 13: above 10 days).
        self.assertEqual(bands["CRITICAL"], 6)
        self.assertEqual(report["total_open"], 6)

    def test_aging_report_groups_for_scoped_user(self):
        report = aging_report(self.lead, TODAY)
        self.assertEqual(report["total_open"], 6)

    def test_project_report_groups(self):
        rows = project_report(self.admin, TODAY)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["total"], 6)

    def test_employee_report_groups(self):
        rows = employee_report(self.admin, TODAY)
        self.assertEqual(len(rows), 1, "One developer owns all six")
        self.assertEqual(rows[0]["total_open"], 6)
        self.assertEqual(rows[0]["in_progress"], 6)

    def test_team_workload_groups(self):
        rows = team_workload(self.lead, TODAY)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["total_open"], 6)

    def test_kpis_are_consistent_with_reports(self):
        """The dashboard and the aging report must not disagree."""
        kpis = dashboard_kpis(self.admin, TODAY)
        aging = aging_report(self.admin, TODAY)
        self.assertEqual(kpis["total_open"], aging["total_open"])
        self.assertEqual(kpis["in_progress"], 6)
