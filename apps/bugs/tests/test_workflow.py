"""Status transition and validation tests (spec 29, 30)."""

import datetime

from django.test import SimpleTestCase

from apps.bugs.constants import BugStatus
from apps.bugs.services import workflow
from common.exceptions.domain import TransitionNotAllowed, WorkflowValidationError

S = BugStatus


class _FakeBug:
    """Stand-in for a Bug row; workflow only reads attributes."""

    def __init__(self, **kwargs):
        defaults = {
            "status": S.NEW, "owner_id": None, "owner": None,
            "resolution": "", "latest_remarks": "", "hold_reason": "",
            "rejection_reason": "", "root_cause": "", "resolved_date": None,
            "verification_result": "", "closure_remarks": "",
            "closed_date": None, "closed_by": None, "closed_by_id": None,
        }
        defaults.update(kwargs)
        for key, value in defaults.items():
            setattr(self, key, value)


class TransitionMapTests(SimpleTestCase):
    def test_every_status_has_an_entry(self):
        for status in S.values:
            self.assertIn(status, workflow.ALLOWED_TRANSITIONS,
                          f"{status} missing from the transition map")

    def test_spec_29_legal_transitions(self):
        legal = [
            (S.NEW, S.ASSIGNED), (S.NEW, S.REJECTED),
            (S.ASSIGNED, S.IN_PROGRESS), (S.ASSIGNED, S.ON_HOLD),
            (S.IN_PROGRESS, S.TESTING), (S.IN_PROGRESS, S.ON_HOLD),
            (S.ON_HOLD, S.IN_PROGRESS),
            (S.ON_HOLD, S.TESTING),
            (S.IN_PROGRESS, S.PENDING),
            (S.PENDING, S.IN_PROGRESS),
            (S.PENDING, S.TESTING),
            (S.TESTING, S.RESOLVED), (S.TESTING, S.IN_PROGRESS), (S.TESTING, S.ASSIGNED),
            (S.RESOLVED, S.CLOSED), (S.RESOLVED, S.REOPENED),
            (S.CLOSED, S.REOPENED),
            (S.REOPENED, S.ASSIGNED), (S.REOPENED, S.IN_PROGRESS),
        ]
        for src, dst in legal:
            self.assertTrue(workflow.can_transition(src, dst), f"{src} -> {dst} should be allowed")

    def test_representative_illegal_transitions(self):
        illegal = [
            (S.NEW, S.CLOSED),          # cannot skip the entire workflow
            (S.NEW, S.IN_PROGRESS),     # must be assigned first
            (S.ASSIGNED, S.CLOSED),
            (S.IN_PROGRESS, S.CLOSED),  # must pass through testing/resolved
            (S.CLOSED, S.IN_PROGRESS),  # only reopen leaves Closed
            (S.REJECTED, S.ASSIGNED),   # rejected is terminal
        ]
        for src, dst in illegal:
            self.assertFalse(workflow.can_transition(src, dst), f"{src} -> {dst} should be blocked")
            with self.assertRaises(TransitionNotAllowed):
                workflow.check_transition(src, dst)

    def test_rejected_is_terminal(self):
        self.assertEqual(workflow.allowed_targets(S.REJECTED), [])

    def test_same_status_is_rejected(self):
        with self.assertRaises(TransitionNotAllowed):
            workflow.check_transition(S.IN_PROGRESS, S.IN_PROGRESS)

    def test_error_reports_allowed_targets(self):
        with self.assertRaises(TransitionNotAllowed) as ctx:
            workflow.check_transition(S.NEW, S.CLOSED)
        self.assertEqual(sorted(ctx.exception.allowed), sorted([S.ASSIGNED, S.REJECTED]))


class RequiredFieldTests(SimpleTestCase):
    def test_in_progress_requires_owner(self):
        with self.assertRaises(WorkflowValidationError) as ctx:
            workflow.check_required_fields(_FakeBug(status=S.ASSIGNED), S.IN_PROGRESS)
        self.assertIn("owner", ctx.exception.errors)

    def test_in_progress_passes_with_owner(self):
        bug = _FakeBug(status=S.ASSIGNED, owner_id=5)
        workflow.check_required_fields(bug, S.IN_PROGRESS)

    def test_on_hold_requires_reason(self):
        with self.assertRaises(WorkflowValidationError) as ctx:
            workflow.check_required_fields(_FakeBug(status=S.ASSIGNED), S.ON_HOLD)
        self.assertIn("hold_reason", ctx.exception.errors)

    def test_testing_requires_resolution_and_remarks(self):
        with self.assertRaises(WorkflowValidationError) as ctx:
            workflow.check_required_fields(_FakeBug(status=S.IN_PROGRESS), S.TESTING)
        self.assertEqual(set(ctx.exception.errors), {"resolution", "latest_remarks"})

    def test_resolved_requires_three_fields(self):
        with self.assertRaises(WorkflowValidationError) as ctx:
            workflow.check_required_fields(_FakeBug(status=S.TESTING), S.RESOLVED)
        self.assertEqual(set(ctx.exception.errors), {"root_cause", "resolution", "resolved_date"})

    def test_close_reports_all_six_missing_fields_at_once(self):
        """Spec 30: closing needs six fields. All must surface together."""
        with self.assertRaises(WorkflowValidationError) as ctx:
            workflow.check_required_fields(_FakeBug(status=S.RESOLVED), S.CLOSED)
        self.assertEqual(
            set(ctx.exception.errors),
            {"root_cause", "resolution", "verification_result",
             "closure_remarks", "closed_date", "closed_by"},
        )

    def test_close_succeeds_when_complete(self):
        bug = _FakeBug(
            status=S.RESOLVED, root_cause="Bad serializer mapping",
            resolution="Corrected mapping", verification_result="PASSED",
            closure_remarks="Verified in production",
            closed_date=datetime.date(2026, 9, 15), closed_by_id=2,
        )
        workflow.check_required_fields(bug, S.CLOSED)

    def test_blank_string_counts_as_missing(self):
        """Whitespace must not satisfy a required text field."""
        bug = _FakeBug(status=S.ASSIGNED, hold_reason="   ")
        with self.assertRaises(WorkflowValidationError):
            workflow.check_required_fields(bug, S.ON_HOLD)

    def test_reopen_reason_comes_from_payload(self):
        bug = _FakeBug(status=S.CLOSED)
        with self.assertRaises(WorkflowValidationError) as ctx:
            workflow.check_required_fields(bug, S.REOPENED)
        self.assertIn("reopen_reason", ctx.exception.errors)
        # Supplied in the same request -> accepted.
        workflow.check_required_fields(bug, S.REOPENED, payload={"reopen_reason": "Recurred in UAT"})

    def test_payload_satisfies_missing_model_field(self):
        bug = _FakeBug(status=S.ASSIGNED)
        workflow.check_required_fields(bug, S.ON_HOLD, payload={"hold_reason": "Awaiting vendor"})


class ValidateTransitionTests(SimpleTestCase):
    def test_legality_is_checked_before_completeness(self):
        """An illegal move raises TransitionNotAllowed even when fields are absent."""
        with self.assertRaises(TransitionNotAllowed):
            workflow.validate_transition(_FakeBug(status=S.NEW), S.CLOSED)

    def test_full_gate_passes(self):
        workflow.validate_transition(_FakeBug(status=S.NEW), S.ASSIGNED)
