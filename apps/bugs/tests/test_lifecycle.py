"""End-to-end bug lifecycle tests (spec 4, 29, 30, 65)."""

import datetime

from django.test import TestCase

from apps.bugs.constants import BugStatus, TestResult, VerificationResult
from apps.bugs.models import (
    Bug,
    BugAssignmentHistory,
    BugReopenHistory,
    BugStatusHistory,
    BugTestingHistory,
    BugUpdate,
)
from apps.bugs.services import (
    add_update,
    assign_bug,
    change_status,
    close_bug,
    create_bug,
    record_test,
    reopen_bug,
    resolve_bug,
)
from apps.bugs.tests.factories import make_masters, make_user
from apps.masters.models import RootCauseTypeMaster
from common.exceptions.domain import (
    ImmutableRecordError,
    TransitionNotAllowed,
    WorkflowValidationError,
)


class LifecycleTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.reporter = make_user("finance")
        cls.lead = make_user("lead")
        cls.dev = make_user("kiran")
        cls.qa = make_user("qa")
        cls.project, cls.module, cls.priority, cls.severity = make_masters()
        cls.rct = RootCauseTypeMaster.objects.create(code="CODING_ISSUE", name="Coding Issue")

    def _new_bug(self, **kwargs):
        return create_bug(
            actor=self.reporter, project=self.project, module=self.module,
            title=kwargs.pop("title", "Customer approval not loading"),
            description=kwargs.pop("description", "Approval page gives 500 error"),
            priority=self.priority, severity=self.severity,
            reported_date=datetime.date(2026, 9, 14), **kwargs,
        )

    def test_creation_generates_number_and_history(self):
        bug = self._new_bug()
        self.assertTrue(bug.bug_no.startswith("BUG-2609-"))
        self.assertEqual(bug.status, BugStatus.NEW)
        self.assertEqual(bug.reported_by, self.reporter)
        # Opening history row exists with a blank from_status.
        history = BugStatusHistory.objects.get(bug=bug)
        self.assertEqual(history.from_status, "")
        self.assertEqual(history.to_status, BugStatus.NEW)

    def test_expected_closure_defaults_from_priority_sla(self):
        self.priority.sla_days = 3
        self.priority.save()
        bug = self._new_bug()
        self.assertEqual(bug.expected_closure_date, datetime.date(2026, 9, 17))

    def test_full_happy_path(self):
        """New -> Assigned -> In Progress -> Testing -> Resolved -> Closed."""
        bug = self._new_bug()

        assign_bug(bug=bug, new_owner=self.dev, actor=self.lead, remarks="Please analyse")
        bug.refresh_from_db()
        self.assertEqual(bug.status, BugStatus.ASSIGNED)
        self.assertEqual(bug.owner, self.dev)
        self.assertEqual(BugAssignmentHistory.objects.filter(bug=bug).count(), 1)

        change_status(bug=bug, to_status=BugStatus.IN_PROGRESS, actor=self.dev)
        bug.refresh_from_db()
        self.assertEqual(bug.status, BugStatus.IN_PROGRESS)

        add_update(bug=bug, actor=self.dev, update_text="500 error identified",
                   remarks="Serializer mapping wrong", next_action="Fix serializer")
        bug.refresh_from_db()
        self.assertEqual(bug.next_action, "Fix serializer")

        # Testing requires a resolution and remarks (spec 30).
        bug.resolution = "Serializer mapping corrected"
        bug.save()
        change_status(bug=bug, to_status=BugStatus.TESTING, actor=self.dev)
        bug.refresh_from_db()
        self.assertEqual(bug.status, BugStatus.TESTING)

        record_test(bug=bug, actor=self.qa, test_result=TestResult.PASSED,
                    test_remarks="Verified in UAT")
        bug.refresh_from_db()
        self.assertEqual(bug.verification_result, VerificationResult.PASSED)
        self.assertEqual(BugTestingHistory.objects.filter(bug=bug).count(), 1)

        resolve_bug(bug=bug, actor=self.dev, root_cause="Incorrect serializer mapping",
                    resolution="Serializer mapping corrected", root_cause_type=self.rct)
        bug.refresh_from_db()
        self.assertEqual(bug.status, BugStatus.RESOLVED)

        close_bug(bug=bug, actor=self.lead, closure_remarks="Verified in production")
        bug.refresh_from_db()
        self.assertEqual(bug.status, BugStatus.CLOSED)
        self.assertEqual(bug.closed_by, self.lead)
        self.assertIsNotNone(bug.closed_date)

    def test_cannot_close_without_required_fields(self):
        """Spec 30: all six closure requirements reported together."""
        bug = self._new_bug()
        assign_bug(bug=bug, new_owner=self.dev, actor=self.lead)
        change_status(bug=bug, to_status=BugStatus.IN_PROGRESS, actor=self.dev)
        bug.resolution = "Fixed"
        bug.save()
        change_status(bug=bug, to_status=BugStatus.TESTING, actor=self.dev)
        resolve_bug(bug=bug, actor=self.dev, root_cause="RC", resolution="Fixed")
        bug.refresh_from_db()

        # Missing verification_result and closure_remarks.
        with self.assertRaises(WorkflowValidationError) as ctx:
            change_status(bug=bug, to_status=BugStatus.CLOSED, actor=self.lead)
        self.assertIn("verification_result", ctx.exception.errors)
        self.assertIn("closure_remarks", ctx.exception.errors)
        bug.refresh_from_db()
        self.assertEqual(bug.status, BugStatus.RESOLVED, "Status must not change on failure")

    def test_illegal_transition_is_blocked(self):
        bug = self._new_bug()
        with self.assertRaises(TransitionNotAllowed):
            change_status(bug=bug, to_status=BugStatus.CLOSED, actor=self.lead)
        bug.refresh_from_db()
        self.assertEqual(bug.status, BugStatus.NEW)

    def test_failed_test_returns_bug_to_in_progress(self):
        bug = self._new_bug()
        assign_bug(bug=bug, new_owner=self.dev, actor=self.lead)
        change_status(bug=bug, to_status=BugStatus.IN_PROGRESS, actor=self.dev)
        bug.resolution = "Attempted fix"
        bug.save()
        change_status(bug=bug, to_status=BugStatus.TESTING, actor=self.dev)

        record_test(bug=bug, actor=self.qa, test_result=TestResult.FAILED,
                    test_remarks="Still failing on submit")
        bug.refresh_from_db()
        self.assertEqual(bug.status, BugStatus.IN_PROGRESS)
        self.assertEqual(bug.verification_result, VerificationResult.FAILED)

    def test_reopen_preserves_closure_record(self):
        bug = self._new_bug()
        assign_bug(bug=bug, new_owner=self.dev, actor=self.lead)
        change_status(bug=bug, to_status=BugStatus.IN_PROGRESS, actor=self.dev)
        bug.resolution = "Fixed"
        bug.save()
        change_status(bug=bug, to_status=BugStatus.TESTING, actor=self.dev)
        resolve_bug(bug=bug, actor=self.dev, root_cause="RC", resolution="Fixed")
        record_test(bug=bug, actor=self.qa, test_result=TestResult.PASSED)
        bug.refresh_from_db()
        close_bug(bug=bug, actor=self.lead, closure_remarks="Done")
        bug.refresh_from_db()
        original_closed_date = bug.closed_date

        reopen_bug(bug=bug, actor=self.qa, reopen_reason="Recurred in production")
        bug.refresh_from_db()
        self.assertEqual(bug.status, BugStatus.REOPENED)
        self.assertEqual(bug.reopen_count, 1)
        # Spec 65: the prior closure is history, not something to erase.
        self.assertEqual(bug.closed_date, original_closed_date)
        self.assertEqual(bug.closure_remarks, "Done")
        self.assertEqual(BugReopenHistory.objects.filter(bug=bug).count(), 1)

    def test_reassigning_a_reopened_bug_auto_transitions_to_assigned(self):
        """assign_bug() auto-transitions NEW -> ASSIGNED; it must do the same
        for REOPENED -> ASSIGNED (spec 29 allows both), otherwise a reassigned
        reopened bug is stuck showing an owner while still reading REOPENED --
        the exact "owner but wrong status" inconsistency the auto-transition
        exists to prevent."""
        bug = self._new_bug()
        assign_bug(bug=bug, new_owner=self.dev, actor=self.lead)
        change_status(bug=bug, to_status=BugStatus.IN_PROGRESS, actor=self.dev)
        bug.resolution = "Fixed"
        bug.save()
        change_status(bug=bug, to_status=BugStatus.TESTING, actor=self.dev)
        resolve_bug(bug=bug, actor=self.dev, root_cause="RC", resolution="Fixed")
        record_test(bug=bug, actor=self.qa, test_result=TestResult.PASSED)
        bug.refresh_from_db()
        close_bug(bug=bug, actor=self.lead, closure_remarks="Done")
        reopen_bug(bug=bug, actor=self.qa, reopen_reason="Recurred")
        bug.refresh_from_db()
        self.assertEqual(bug.status, BugStatus.REOPENED)

        assign_bug(bug=bug, new_owner=self.dev, actor=self.lead, remarks="Please recheck")
        bug.refresh_from_db()
        self.assertEqual(bug.status, BugStatus.ASSIGNED)

    def test_reassignment_records_both_owners(self):
        bug = self._new_bug()
        assign_bug(bug=bug, new_owner=self.dev, actor=self.lead)
        other = make_user("arun")
        assign_bug(bug=bug, new_owner=other, actor=self.lead, remarks="Load balancing")
        bug.refresh_from_db()
        self.assertEqual(bug.owner, other)
        latest = BugAssignmentHistory.objects.filter(bug=bug).order_by("-id").first()
        self.assertEqual(latest.from_owner, self.dev)
        self.assertEqual(latest.to_owner, other)


class ImmutabilityTests(TestCase):
    """Spec 65: history is append-only."""

    @classmethod
    def setUpTestData(cls):
        cls.user = make_user("dev")
        cls.project, cls.module, cls.priority, cls.severity = make_masters()
        cls.bug = create_bug(
            actor=cls.user, project=cls.project, priority=cls.priority,
            severity=cls.severity, title="T", description="D",
            reported_date=datetime.date(2026, 9, 14),
        )

    def test_bug_update_cannot_be_modified(self):
        update = add_update(bug=self.bug, actor=self.user, update_text="Original text")
        update.update_text = "Rewritten"
        with self.assertRaises(ImmutableRecordError):
            update.save()
        update.refresh_from_db()
        self.assertEqual(update.update_text, "Original text")

    def test_bug_update_cannot_be_deleted(self):
        update = add_update(bug=self.bug, actor=self.user, update_text="Keep me")
        with self.assertRaises(ImmutableRecordError):
            update.delete()
        self.assertTrue(BugUpdate.objects.filter(pk=update.pk).exists())

    def test_status_history_is_immutable(self):
        history = BugStatusHistory.objects.filter(bug=self.bug).first()
        history.to_status = BugStatus.CLOSED
        with self.assertRaises(ImmutableRecordError):
            history.save()

    def test_daily_updates_accumulate_rather_than_overwrite(self):
        """Spec 10: never overwrite the previous day's update."""
        add_update(bug=self.bug, actor=self.user, update_text="Day 1")
        add_update(bug=self.bug, actor=self.user, update_text="Day 2")
        add_update(bug=self.bug, actor=self.user, update_text="Day 3")
        texts = list(BugUpdate.objects.filter(bug=self.bug)
                     .order_by("id").values_list("update_text", flat=True))
        self.assertEqual(texts, ["Day 1", "Day 2", "Day 3"])
        # The bug row carries only the latest, for fast list loading.
        self.bug.refresh_from_db()
        self.assertEqual(self.bug.latest_remarks, "Day 3")

    def test_latest_update_fields_track_the_newest_update(self):
        add_update(bug=self.bug, actor=self.user, update_text="First",
                   remarks="First remark")
        self.bug.refresh_from_db()
        self.assertEqual(self.bug.latest_remarks, "First remark")
        self.assertIsNotNone(self.bug.latest_update_date)
