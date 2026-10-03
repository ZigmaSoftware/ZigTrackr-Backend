"""Aging, overdue and update-pending annotation tests (spec 11, 12, 13).

These run against MariaDB because the whole point of the DateDiff wrapper is
database-specific behaviour -- the bugs it guards against would not appear on
SQLite.
"""

import datetime

from django.test import TestCase

from apps.bugs.constants import BugStatus
from apps.bugs.models import Bug
from apps.bugs.selectors.annotations import (
    with_aging,
    with_all_computed,
    with_overdue,
    with_update_pending,
)
from apps.bugs.tests.factories import make_bug, make_masters, make_user

TODAY = datetime.date(2026, 9, 14)


class AgingTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = make_user("reporter")
        cls.project, cls.module, cls.priority, cls.severity = make_masters()

    def _bug(self, bug_no, **kwargs):
        return make_bug(bug_no=bug_no, reporter=self.user, project=self.project,
                        priority=self.priority, severity=self.severity, **kwargs)

    def test_age_counts_days_since_reported(self):
        self._bug("B1", reported_date=TODAY - datetime.timedelta(days=12))
        bug = with_aging(Bug.objects.all(), today=TODAY).get()
        self.assertEqual(bug.age_days, 12)

    def test_age_across_month_boundary(self):
        """The regression this design exists to prevent.

        Naive DATE subtraction computes 20261001 - 20260930 = 71. DATEDIFF
        gives the true answer of 1. This test fails loudly if anyone swaps the
        DateDiff wrapper for plain arithmetic.
        """
        self._bug("B2", reported_date=datetime.date(2026, 9, 30))
        bug = with_aging(Bug.objects.all(), today=datetime.date(2026, 10, 1)).get()
        self.assertEqual(bug.age_days, 1)

    def test_age_across_year_boundary(self):
        self._bug("B3", reported_date=datetime.date(2026, 12, 31))
        bug = with_aging(Bug.objects.all(), today=datetime.date(2027, 1, 1)).get()
        self.assertEqual(bug.age_days, 1)

    def test_closed_bug_freezes_age_at_closure(self):
        self._bug("B4", status=BugStatus.CLOSED,
                  reported_date=TODAY - datetime.timedelta(days=30),
                  closed_date=TODAY - datetime.timedelta(days=20))
        bug = with_aging(Bug.objects.all(), today=TODAY).get()
        self.assertEqual(bug.age_days, 10)

    def test_aging_bands(self):
        cases = [("A", 0, "NORMAL"), ("B", 2, "NORMAL"), ("C", 3, "ATTENTION"),
                 ("D", 5, "ATTENTION"), ("E", 6, "WARNING"), ("F", 10, "WARNING"),
                 ("G", 11, "CRITICAL"), ("H", 45, "CRITICAL")]
        for bug_no, age, _band in cases:
            self._bug(bug_no, reported_date=TODAY - datetime.timedelta(days=age))
        annotated = {b.bug_no: b.aging_band for b in with_aging(Bug.objects.all(), today=TODAY)}
        for bug_no, _age, expected in cases:
            self.assertEqual(annotated[bug_no], expected, f"{bug_no} banded wrongly")

    def test_age_is_sortable(self):
        self._bug("OLD", reported_date=TODAY - datetime.timedelta(days=20))
        self._bug("NEW", reported_date=TODAY - datetime.timedelta(days=1))
        ordered = list(with_aging(Bug.objects.all(), today=TODAY).order_by("-age_days")
                       .values_list("bug_no", flat=True))
        self.assertEqual(ordered, ["OLD", "NEW"])


class OverdueTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = make_user("reporter2")
        cls.project, cls.module, cls.priority, cls.severity = make_masters()

    def _bug(self, bug_no, **kwargs):
        return make_bug(bug_no=bug_no, reporter=self.user, project=self.project,
                        priority=self.priority, severity=self.severity, **kwargs)

    def test_overdue_days_matches_spec_example(self):
        """Spec 12: expected 12 Sep, today 14 Sep, In Progress -> 2 days."""
        self._bug("O1", status=BugStatus.IN_PROGRESS,
                  expected_closure_date=datetime.date(2026, 9, 12))
        bug = with_overdue(Bug.objects.all(), today=TODAY).get()
        self.assertTrue(bug.is_overdue)
        self.assertEqual(bug.overdue_days, 2)

    def test_closed_bug_past_date_is_not_overdue(self):
        """Spec 12 requires status != Closed, regardless of the date."""
        self._bug("O2", status=BugStatus.CLOSED,
                  expected_closure_date=datetime.date(2026, 9, 1),
                  closed_date=datetime.date(2026, 9, 2))
        bug = with_overdue(Bug.objects.all(), today=TODAY).get()
        self.assertFalse(bug.is_overdue)
        self.assertEqual(bug.overdue_days, 0)

    def test_rejected_bug_is_not_overdue(self):
        self._bug("O3", status=BugStatus.REJECTED,
                  expected_closure_date=datetime.date(2026, 9, 1))
        bug = with_overdue(Bug.objects.all(), today=TODAY).get()
        self.assertFalse(bug.is_overdue)

    def test_future_and_null_dates_are_not_overdue(self):
        self._bug("O4", status=BugStatus.IN_PROGRESS,
                  expected_closure_date=TODAY + datetime.timedelta(days=3))
        self._bug("O5", status=BugStatus.IN_PROGRESS, expected_closure_date=None)
        flags = {b.bug_no: b.is_overdue for b in with_overdue(Bug.objects.all(), today=TODAY)}
        self.assertFalse(flags["O4"])
        self.assertFalse(flags["O5"])

    def test_due_today_is_not_yet_overdue(self):
        self._bug("O6", status=BugStatus.IN_PROGRESS, expected_closure_date=TODAY)
        bug = with_overdue(Bug.objects.all(), today=TODAY).get()
        self.assertFalse(bug.is_overdue)

    def test_overdue_across_month_boundary(self):
        self._bug("O7", status=BugStatus.IN_PROGRESS,
                  expected_closure_date=datetime.date(2026, 9, 30))
        bug = with_overdue(Bug.objects.all(), today=datetime.date(2026, 10, 2)).get()
        self.assertEqual(bug.overdue_days, 2)

    def test_overdue_is_filterable(self):
        self._bug("LATE", status=BugStatus.IN_PROGRESS,
                  expected_closure_date=datetime.date(2026, 9, 1))
        self._bug("FINE", status=BugStatus.IN_PROGRESS,
                  expected_closure_date=TODAY + datetime.timedelta(days=5))
        late = list(with_overdue(Bug.objects.all(), today=TODAY)
                    .filter(is_overdue=True).values_list("bug_no", flat=True))
        self.assertEqual(late, ["LATE"])


class UpdatePendingTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = make_user("dev")
        cls.project, cls.module, cls.priority, cls.severity = make_masters()

    def _bug(self, bug_no, **kwargs):
        return make_bug(bug_no=bug_no, reporter=self.user, project=self.project,
                        priority=self.priority, severity=self.severity, **kwargs)

    def test_active_bug_without_update_today_is_pending(self):
        self._bug("U1", status=BugStatus.IN_PROGRESS,
                  latest_update_date=TODAY - datetime.timedelta(days=1))
        bug = with_update_pending(Bug.objects.all(), today=TODAY).get()
        self.assertTrue(bug.is_update_pending)
        self.assertEqual(bug.days_since_update, 1)

    def test_updated_today_is_not_pending(self):
        self._bug("U2", status=BugStatus.IN_PROGRESS, latest_update_date=TODAY)
        bug = with_update_pending(Bug.objects.all(), today=TODAY).get()
        self.assertFalse(bug.is_update_pending)

    def test_never_updated_active_bug_is_pending(self):
        self._bug("U3", status=BugStatus.ASSIGNED, latest_update_date=None,
                  reported_date=TODAY - datetime.timedelta(days=3))
        bug = with_update_pending(Bug.objects.all(), today=TODAY).get()
        self.assertTrue(bug.is_update_pending)
        self.assertEqual(bug.days_since_update, 3)

    def test_only_spec_11_statuses_require_updates(self):
        """Assigned, In Progress, Testing and On Hold -- not New, Closed etc."""
        pending_statuses = [BugStatus.ASSIGNED, BugStatus.IN_PROGRESS,
                            BugStatus.TESTING, BugStatus.ON_HOLD]
        exempt_statuses = [BugStatus.NEW, BugStatus.RESOLVED, BugStatus.CLOSED,
                           BugStatus.REJECTED, BugStatus.REOPENED]
        for i, status in enumerate(pending_statuses + exempt_statuses):
            self._bug(f"S{i}", status=status, latest_update_date=None)
        rows = {b.status: b.is_update_pending
                for b in with_update_pending(Bug.objects.all(), today=TODAY)}
        for status in pending_statuses:
            self.assertTrue(rows[status], f"{status} should require a daily update")
        for status in exempt_statuses:
            self.assertFalse(rows[status], f"{status} should not require a daily update")

    def test_update_pending_is_filterable(self):
        self._bug("P1", status=BugStatus.IN_PROGRESS, latest_update_date=None)
        self._bug("P2", status=BugStatus.IN_PROGRESS, latest_update_date=TODAY)
        pending = list(with_update_pending(Bug.objects.all(), today=TODAY)
                       .filter(is_update_pending=True).values_list("bug_no", flat=True))
        self.assertEqual(pending, ["P1"])


class CombinedAnnotationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = make_user("combo")
        cls.project, cls.module, cls.priority, cls.severity = make_masters()

    def test_all_annotations_compose(self):
        make_bug(bug_no="C1", reporter=self.user, project=self.project,
                 priority=self.priority, severity=self.severity,
                 status=BugStatus.IN_PROGRESS,
                 reported_date=TODAY - datetime.timedelta(days=8),
                 expected_closure_date=TODAY - datetime.timedelta(days=3),
                 latest_update_date=TODAY - datetime.timedelta(days=2))
        bug = with_all_computed(Bug.objects.all(), today=TODAY).get()
        self.assertEqual(bug.age_days, 8)
        self.assertEqual(bug.aging_band, "WARNING")
        self.assertTrue(bug.is_overdue)
        self.assertEqual(bug.overdue_days, 3)
        self.assertTrue(bug.is_update_pending)
        self.assertEqual(bug.days_since_update, 2)
