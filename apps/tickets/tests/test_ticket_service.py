"""Ticket confirmation and the bridge into the existing Bug domain."""

from django.test import TestCase
from unittest.mock import patch

from apps.accounts.services.system_user import get_mail_intake_user
from apps.bugs.constants import BugStatus
from apps.bugs.models import Bug, BugStatusHistory
from apps.tickets.constants import ClassificationStatus, TicketStatus, TicketType
from apps.tickets.services.ticket_service import (
    close_ticket,
    complete_service_work,
    confirm_classification,
    review_ticket,
    start_service_work,
)
from apps.tickets.tests.factories import make_masters, make_ticket, make_user
from common.exceptions.domain import WorkflowValidationError
from common.permissions.codenames import ROLE_DEVELOPER, ROLE_PERMISSIONS
from common.permissions.scoping import can_mutate_ticket, scope_ticket_queryset


class ConfirmBugTests(TestCase):
    def setUp(self):
        self.admin = make_user(username="admin.user")
        self.project, self.module, self.priority, self.severity = make_masters()
        self.ticket = make_ticket(ticket_type=TicketType.UNKNOWN, needs_review=True)

    def test_confirming_a_bug_creates_a_real_bug_row(self):
        ticket = confirm_classification(
            ticket=self.ticket, actor=self.admin, ticket_type=TicketType.BUG,
            project=self.project, module=self.module,
            priority=self.priority, severity=self.severity,
        )
        self.assertEqual(Bug.objects.count(), 1)
        bug = Bug.objects.get()
        self.assertEqual(ticket.bug_id, bug.pk)
        self.assertTrue(bug.bug_no.startswith("BUG-"))
        self.assertEqual(bug.status, BugStatus.NEW)
        self.assertEqual(bug.project_id, self.project.pk)

    def test_bug_enters_the_existing_workflow_with_an_opening_history_row(self):
        """An email-born bug must be an ordinary bug in every respect."""
        confirm_classification(
            ticket=self.ticket, actor=self.admin, ticket_type=TicketType.BUG,
            project=self.project, priority=self.priority, severity=self.severity,
        )
        bug = Bug.objects.get()
        history = BugStatusHistory.objects.filter(bug=bug)
        self.assertEqual(history.count(), 1)
        opening = history.get()
        self.assertEqual(opening.from_status, "")
        self.assertEqual(opening.to_status, BugStatus.NEW)
        # The confirming admin acted; the system user merely reported.
        self.assertEqual(opening.changed_by_id, self.admin.pk)

    def test_reporter_is_the_system_user_while_actor_is_the_admin(self):
        confirm_classification(
            ticket=self.ticket, actor=self.admin, ticket_type=TicketType.BUG,
            project=self.project, priority=self.priority, severity=self.severity,
        )
        bug = Bug.objects.get()
        self.assertEqual(bug.reported_by_id, get_mail_intake_user().pk)
        self.assertNotEqual(bug.reported_by_id, self.admin.pk)

    def test_ticket_is_marked_human_confirmed(self):
        ticket = confirm_classification(
            ticket=self.ticket, actor=self.admin, ticket_type=TicketType.BUG,
            project=self.project, priority=self.priority, severity=self.severity,
        )
        self.assertEqual(ticket.classification_status, ClassificationStatus.HUMAN_CONFIRMED)
        self.assertFalse(ticket.needs_review)
        self.assertEqual(ticket.confirmed_by_id, self.admin.pk)
        self.assertIsNotNone(ticket.confirmed_at)

    def test_classification_method_is_not_overwritten_on_confirm(self):
        """Method records HOW it was classified; status records whether a human agreed."""
        original = self.ticket.classification_method
        ticket = confirm_classification(
            ticket=self.ticket, actor=self.admin, ticket_type=TicketType.BUG,
            project=self.project, priority=self.priority, severity=self.severity,
        )
        self.assertEqual(ticket.classification_method, original)

    def test_missing_project_is_refused(self):
        with self.assertRaises(WorkflowValidationError):
            confirm_classification(
                ticket=self.ticket, actor=self.admin, ticket_type=TicketType.BUG,
                project=None, priority=self.priority, severity=self.severity,
            )
        self.assertEqual(Bug.objects.count(), 0)

    def test_missing_priority_or_severity_is_refused(self):
        for missing in ("priority", "severity"):
            kwargs = {
                "project": self.project,
                "priority": self.priority,
                "severity": self.severity,
            }
            kwargs[missing] = None
            with self.assertRaises(WorkflowValidationError):
                confirm_classification(
                    ticket=self.ticket, actor=self.admin,
                    ticket_type=TicketType.BUG, **kwargs
                )
        self.assertEqual(Bug.objects.count(), 0)

    def test_confirming_twice_creates_only_one_bug(self):
        for _ in range(2):
            confirm_classification(
                ticket=self.ticket, actor=self.admin, ticket_type=TicketType.BUG,
                project=self.project, priority=self.priority, severity=self.severity,
            )
        self.assertEqual(Bug.objects.count(), 1)


class ConfirmOtherTypesTests(TestCase):
    def setUp(self):
        self.admin = make_user(username="admin.user")
        self.project, self.module, self.priority, self.severity = make_masters()

    def test_service_request_creates_no_bug(self):
        ticket = make_ticket()
        ticket = confirm_classification(
            ticket=ticket, actor=self.admin, ticket_type=TicketType.SERVICE_REQUEST,
        )
        self.assertEqual(Bug.objects.count(), 0)
        self.assertEqual(ticket.status, TicketStatus.CONFIRMED)

    def test_service_request_does_not_require_bug_masters(self):
        """A service request must not demand a severity."""
        ticket = make_ticket()
        confirm_classification(
            ticket=ticket, actor=self.admin, ticket_type=TicketType.SERVICE_REQUEST,
        )
        self.assertEqual(ticket.ticket_type, TicketType.SERVICE_REQUEST)

    def test_confirming_legacy_assigned_service_releases_it_for_work(self):
        owner = make_user(username="assigned_developer")
        ticket = make_ticket(
            ticket_type=TicketType.SERVICE_REQUEST,
            needs_review=True,
            owner=owner,
        )
        confirm_classification(
            ticket=ticket, actor=self.admin,
            ticket_type=TicketType.SERVICE_REQUEST,
        )
        ticket.refresh_from_db()
        self.assertFalse(ticket.needs_review)
        self.assertEqual(ticket.owner, owner)
        self.assertEqual(ticket.status, TicketStatus.CONFIRMED)
        start_service_work(ticket=ticket, actor=owner)
        ticket.refresh_from_db()
        self.assertEqual(ticket.status, TicketStatus.IN_PROGRESS)

    def test_access_request_awaits_approval_and_grants_nothing(self):
        """Confirming a classification is not approving access (spec 29)."""
        ticket = make_ticket()
        ticket = confirm_classification(
            ticket=ticket, actor=self.admin, ticket_type=TicketType.ACCESS_REQUEST,
        )
        self.assertEqual(ticket.status, TicketStatus.PENDING_APPROVAL)
        self.assertEqual(Bug.objects.count(), 0)


class ServiceWorkflowTests(TestCase):
    def setUp(self):
        self.admin = make_user(username="admin.user")

    def test_service_request_can_start_complete_and_close(self):
        ticket = make_ticket(
            ticket_type=TicketType.SERVICE_REQUEST,
            needs_review=False,
            status=TicketStatus.NEW,
            owner=self.admin,
        )

        start_service_work(ticket=ticket, actor=self.admin, remarks="Accepted by IT.")
        ticket.refresh_from_db()
        self.assertEqual(ticket.status, TicketStatus.IN_PROGRESS)

        complete_service_work(ticket=ticket, actor=self.admin, remarks="Login created.")
        ticket.refresh_from_db()
        self.assertEqual(ticket.status, TicketStatus.COMPLETED)

        close_ticket(ticket=ticket, actor=self.admin, remarks="Verified with requester.")
        ticket.refresh_from_db()
        self.assertEqual(ticket.status, TicketStatus.CLOSED)
        self.assertEqual(ticket.updates.count(), 3)
        self.assertEqual(
            list(ticket.activities.values_list("event_type", flat=True)),
            ["WORK_STARTED", "SERVICE_COMPLETED", "TICKET_CLOSED"],
        )

    def test_bug_ticket_cannot_use_service_workflow(self):
        ticket = make_ticket(ticket_type=TicketType.BUG, needs_review=False, status=TicketStatus.NEW)
        with self.assertRaises(WorkflowValidationError):
            start_service_work(ticket=ticket, actor=self.admin)

    def test_service_request_needs_an_owner_before_work(self):
        ticket = make_ticket(ticket_type=TicketType.SERVICE_REQUEST, needs_review=False)
        with self.assertRaises(WorkflowValidationError):
            start_service_work(ticket=ticket, actor=self.admin)
        self.assertEqual(ticket.status, TicketStatus.NEW)


class AssignedTicketVisibilityTests(TestCase):
    def test_lead_keeps_access_to_assigned_team_ticket(self):
        from apps.bugs.tests.factories import make_team

        team = make_team()
        lead = make_user(username="lead", team=team)
        developer = make_user(username="developer", team=team)
        outsider = make_user(username="outsider")
        ticket = make_ticket(
            ticket_type=TicketType.SERVICE_REQUEST,
            needs_review=False,
            status=TicketStatus.ASSIGNED,
            owner=developer,
        )

        with patch("common.permissions.scoping.has_permission", side_effect=lambda user, code: user == lead and code == "tickets.ticket.classify"):
            self.assertTrue(scope_ticket_queryset(type(ticket).objects.all(), lead).filter(pk=ticket.pk).exists())
            self.assertTrue(can_mutate_ticket(lead, ticket))
            self.assertTrue(scope_ticket_queryset(type(ticket).objects.all(), developer).filter(pk=ticket.pk).exists())
            self.assertTrue(can_mutate_ticket(developer, ticket))
            self.assertFalse(scope_ticket_queryset(type(ticket).objects.all(), outsider).filter(pk=ticket.pk).exists())

    def test_developer_can_implement_only_after_approval_permission_is_granted(self):
        self.assertIn("access.request.implement", ROLE_PERMISSIONS[ROLE_DEVELOPER])
        self.assertNotIn("access.request.approve", ROLE_PERMISSIONS[ROLE_DEVELOPER])


class TicketNumberIssuedOnAssignmentTests(TestCase):
    """A request carries a reference; assignment is what makes it a ticket."""

    def setUp(self):
        self.admin = make_user(username="number.admin")
        self.developer = make_user(username="number.dev")
        self.project, self.module, self.priority, self.severity = make_masters()

    def test_unrouted_ticket_has_a_ref_and_no_number(self):
        ticket = make_ticket(ticket_type=TicketType.UNKNOWN, needs_review=True)
        self.assertTrue(ticket.ref_no.startswith("REF-"))
        self.assertIsNone(ticket.ticket_no)
        self.assertEqual(ticket.reference, ticket.ref_no)

    def test_review_without_an_owner_still_issues_no_number(self):
        ticket = review_ticket(
            ticket=make_ticket(ticket_type=TicketType.UNKNOWN, needs_review=True),
            actor=self.admin,
            ticket_type=TicketType.SERVICE_REQUEST,
        )
        self.assertIsNone(ticket.ticket_no)

    def test_assigning_an_owner_mints_the_ticket_number(self):
        ticket = make_ticket(ticket_type=TicketType.UNKNOWN, needs_review=True)
        ref = ticket.ref_no

        ticket = review_ticket(
            ticket=ticket,
            actor=self.admin,
            ticket_type=TicketType.SERVICE_REQUEST,
            owner=self.developer,
        )

        self.assertTrue(ticket.ticket_no.startswith("TKT-"))
        self.assertEqual(ticket.ref_no, ref, "the reference must survive assignment")
        self.assertEqual(ticket.reference, ticket.ticket_no)

    def test_a_number_is_never_reissued(self):
        ticket = review_ticket(
            ticket=make_ticket(ticket_type=TicketType.UNKNOWN, needs_review=True),
            actor=self.admin,
            ticket_type=TicketType.SERVICE_REQUEST,
            owner=self.developer,
        )
        issued = ticket.ticket_no

        # Reassignment must not renumber a ticket the requester already holds.
        ticket = review_ticket(
            ticket=ticket,
            actor=self.admin,
            ticket_type=TicketType.SERVICE_REQUEST,
            owner=make_user(username="second.dev"),
        )
        self.assertEqual(ticket.ticket_no, issued)
