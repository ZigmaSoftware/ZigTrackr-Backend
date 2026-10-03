from django.test import Client, TestCase

from apps.accounts.models import UserRole
from apps.bugs.constants import BugStatus, TestResult
from apps.bugs.tests.factories import make_bug, make_masters, make_team
from apps.bugs.tests.test_api import PASSWORD, seed_rbac
from apps.bugs.services.testing_service import record_test
from apps.tickets.constants import TicketStatus, TicketType
from apps.tickets.models import TicketAssignmentHistory
from apps.tickets.services.assignment_service import reassign_ticket
from apps.tickets.tests.factories import make_ticket, make_user
from common.exceptions.domain import WorkflowValidationError


class ReassignmentTests(TestCase):
    def setUp(self):
        self.actor = make_user("reassign_lead")
        self.old_owner = make_user("reassign_old")
        self.new_owner = make_user("reassign_new")

    def test_pending_service_reassignment_records_one_activity_and_history(self):
        ticket = make_ticket(
            ticket_type=TicketType.SERVICE_REQUEST, needs_review=False,
            status=TicketStatus.PENDING, owner=self.old_owner,
        )
        reassign_ticket(
            ticket=ticket, new_owner=self.new_owner, actor=self.actor,
            reason="Move to the on-call specialist",
        )
        ticket.refresh_from_db()
        self.assertEqual(ticket.owner, self.new_owner)
        history = TicketAssignmentHistory.objects.get(ticket=ticket)
        self.assertEqual(history.from_owner, self.old_owner)
        self.assertEqual(history.to_owner, self.new_owner)
        self.assertEqual(history.reason, "Move to the on-call specialist")
        self.assertEqual(ticket.activities.count(), 1)
        self.assertIn("Move to the on-call specialist", ticket.activities.get().description)

    def test_hold_bug_reassignment_keeps_one_activity_with_reason(self):
        project, module, priority, severity = make_masters()
        bug = make_bug(
            bug_no="BUG-REASSIGN-1", reporter=self.actor, project=project,
            module=module, priority=priority, severity=severity,
            status=BugStatus.ON_HOLD, owner=self.old_owner,
        )
        ticket = make_ticket(
            ticket_type=TicketType.BUG, needs_review=False,
            status=TicketStatus.ON_HOLD, owner=self.old_owner, bug=bug,
        )
        reassign_ticket(
            ticket=ticket, new_owner=self.new_owner, actor=self.actor,
            reason="Needs another specialist",
        )
        bug.refresh_from_db()
        ticket.refresh_from_db()
        self.assertEqual(bug.owner, self.new_owner)
        self.assertEqual(ticket.owner, self.new_owner)
        self.assertEqual(ticket.activities.count(), 1)
        activity = ticket.activities.get()
        self.assertEqual(activity.event_type, "TICKET_REASSIGNED")
        self.assertIn("Needs another specialist", activity.description)

    def test_assigned_work_and_reopened_tickets_can_be_reassigned(self):
        for status in (
            TicketStatus.ASSIGNED, TicketStatus.IN_PROGRESS, TicketStatus.REOPENED,
            TicketStatus.PENDING_APPROVAL, TicketStatus.APPROVED,
        ):
            with self.subTest(status=status):
                ticket = make_ticket(
                    ticket_type=TicketType.SERVICE_REQUEST, needs_review=False,
                    status=status, owner=self.old_owner,
                )
                reassign_ticket(
                    ticket=ticket, new_owner=self.new_owner, actor=self.actor,
                    reason="Route to another developer",
                )
                ticket.refresh_from_db()
                self.assertEqual(ticket.owner, self.new_owner)
                self.assertEqual(TicketAssignmentHistory.objects.filter(ticket=ticket).count(), 1)

    def test_reopened_bug_can_be_reassigned(self):
        project, module, priority, severity = make_masters()
        bug = make_bug(
            bug_no="BUG-REOPEN-1", reporter=self.actor, project=project,
            module=module, priority=priority, severity=severity,
            status=BugStatus.REOPENED, owner=self.old_owner,
        )
        ticket = make_ticket(
            ticket_type=TicketType.BUG, needs_review=False,
            status=TicketStatus.TESTING, owner=self.old_owner, bug=bug,
        )
        reassign_ticket(
            ticket=ticket, new_owner=self.new_owner, actor=self.actor,
            reason="Reopened after verification",
        )
        bug.refresh_from_db()
        ticket.refresh_from_db()
        self.assertEqual(bug.owner, self.new_owner)
        self.assertEqual(ticket.owner, self.new_owner)
        self.assertEqual(bug.status, BugStatus.ASSIGNED)

    def test_testing_completed_and_closed_cannot_be_reassigned(self):
        for status in (
            TicketStatus.TESTING, TicketStatus.COMPLETED,
            TicketStatus.CLOSED, TicketStatus.REJECTED,
        ):
            with self.subTest(status=status):
                ticket = make_ticket(
                    ticket_type=TicketType.SERVICE_REQUEST, needs_review=False,
                    status=status, owner=self.old_owner,
                )
                with self.assertRaises(WorkflowValidationError):
                    reassign_ticket(
                        ticket=ticket, new_owner=self.new_owner, actor=self.actor,
                        reason="Try reassignment",
                    )
                self.assertEqual(TicketAssignmentHistory.objects.filter(ticket=ticket).count(), 0)


class ReassignableQueueApiTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        roles = seed_rbac()
        team = make_team("Reassignment team")
        other_team = make_team("Other team")
        cls.admin = make_user("reassign_admin")
        cls.lead = make_user("reassign_queue_lead", team=team)
        cls.owner = make_user("reassign_queue_owner", team=team)
        cls.other_owner = make_user("reassign_other_owner", team=other_team)
        cls.new_owner = make_user("reassign_queue_new_owner", team=team)
        cls.tester = make_user("reassign_queue_tester", team=team)
        for user, role in (
            (cls.admin, "ADMIN"), (cls.lead, "TEAM_LEAD"),
            (cls.owner, "DEVELOPER"), (cls.other_owner, "DEVELOPER"),
            (cls.new_owner, "DEVELOPER"),
            (cls.tester, "TESTER"),
        ):
            user.set_password(PASSWORD)
            user.save()
            UserRole.objects.create(user=user, role=roles[role])

    def login(self, user):
        client = Client()
        response = client.post(
            "/api/v1/auth/login/",
            data={"username": user.username, "password": PASSWORD},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200, response.content)
        return client

    def test_queue_filters_before_pagination_and_respects_team_and_permissions(self):
        eligible = [
            make_ticket(needs_review=False, status=status, owner=self.owner)
            for status in (
                TicketStatus.ASSIGNED, TicketStatus.IN_PROGRESS,
                TicketStatus.PENDING, TicketStatus.ON_HOLD, TicketStatus.REOPENED,
            )
        ]
        for status in (TicketStatus.TESTING, TicketStatus.COMPLETED, TicketStatus.CLOSED):
            make_ticket(needs_review=False, status=status, owner=self.owner)
        make_ticket(needs_review=False, status=TicketStatus.ASSIGNED)
        other_team_ticket = make_ticket(
            needs_review=False, status=TicketStatus.ASSIGNED, owner=self.other_owner,
        )

        response = self.login(self.lead).get("/api/v1/tickets/", {
            "reassignable": "true", "limit": 2,
        })
        self.assertEqual(response.status_code, 200, response.content)
        data = response.json()["data"]
        self.assertEqual(data["count"], len(eligible))
        self.assertEqual(len(data["results"]), 2)
        self.assertTrue(all(row["can_reassign"] for row in data["results"]))

        admin_rows = self.login(self.admin).get(
            "/api/v1/tickets/", {"reassignable": "true"}
        ).json()["data"]["results"]
        self.assertIn(str(other_team_ticket.unique_id), [row["id"] for row in admin_rows])
        developer_data = self.login(self.owner).get(
            "/api/v1/tickets/", {"reassignable": "true"}
        ).json()["data"]
        self.assertEqual(developer_data["count"], 0)

    def test_bug_effective_status_controls_queue_and_reassignment(self):
        project, module, priority, severity = make_masters()
        reopened_bug = make_bug(
            bug_no="BUG-QUEUE-REOPENED", reporter=self.lead, project=project,
            module=module, priority=priority, severity=severity,
            status=BugStatus.REOPENED, owner=self.owner,
        )
        reopened_ticket = make_ticket(
            ticket_type=TicketType.BUG, needs_review=False,
            status=TicketStatus.TESTING, owner=self.owner, bug=reopened_bug,
        )
        testing_bug = make_bug(
            bug_no="BUG-QUEUE-TESTING", reporter=self.lead, project=project,
            module=module, priority=priority, severity=severity,
            status=BugStatus.TESTING, owner=self.owner,
        )
        make_ticket(
            ticket_type=TicketType.BUG, needs_review=False,
            status=TicketStatus.IN_PROGRESS, owner=self.owner, bug=testing_bug,
        )

        client = self.login(self.lead)
        response = client.get("/api/v1/tickets/", {"reassignable": "true"})
        self.assertEqual(response.status_code, 200, response.content)
        rows = response.json()["data"]["results"]
        self.assertEqual([row["id"] for row in rows], [str(reopened_ticket.unique_id)])
        self.assertTrue(rows[0]["can_reassign"])
        self.assertEqual(rows[0]["effective_status"], BugStatus.REOPENED)

        record_test(
            bug=testing_bug, actor=self.tester,
            test_result=TestResult.FAILED, test_remarks="Fix needs more work",
        )
        returned_rows = client.get(
            "/api/v1/tickets/", {"reassignable": "true"}
        ).json()["data"]["results"]
        self.assertEqual(len(returned_rows), 2)
        self.assertIn(BugStatus.IN_PROGRESS, [row["effective_status"] for row in returned_rows])

    def test_newly_assigned_ticket_can_be_reassigned_through_api(self):
        ticket = make_ticket(
            ticket_type=TicketType.SERVICE_REQUEST, needs_review=False,
            status=TicketStatus.ASSIGNED, owner=self.owner,
        )
        client = self.login(self.lead)
        response = client.post(
            f"/api/v1/tickets/{ticket.unique_id}/reassign/",
            data={"owner": str(self.new_owner.unique_id), "reason": "Move to on-call developer"},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200, response.content)
        ticket.refresh_from_db()
        self.assertEqual(ticket.owner, self.new_owner)
