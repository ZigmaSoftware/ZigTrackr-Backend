"""The shared work flow and the public status lookup."""

from django.core import mail as django_mail
from django.core.cache import cache
from django.test import Client, TestCase, override_settings

from apps.accounts.models import UserRole
from apps.bugs.constants import BugStatus, VerificationResult
from apps.bugs.tests.factories import make_bug, make_masters, make_team, make_user
from apps.bugs.tests.test_api import PASSWORD, seed_rbac
from apps.tickets.constants import TicketStatus, TicketType
from apps.tickets.models import OutboundMail
from apps.tickets.tasks import deliver_outbound_mail
from apps.tickets.tests.factories import make_ticket


class WorkTransitionApiTests(TestCase):
    def setUp(self):
        cache.clear()

    def deliver_notice(self, ticket, kind):
        job = OutboundMail.objects.get(ticket=ticket, kind=kind)
        self.assertEqual(django_mail.outbox, [])
        self.assertEqual(deliver_outbound_mail(job.pk), "SENT")
        return job

    @classmethod
    def setUpTestData(cls):
        roles = seed_rbac()
        team = make_team("Flow team")
        cls.developer = make_user("flow_developer", team=team)
        cls.lead = make_user("flow_lead", team=team)
        cls.tester = make_user("flow_tester", team=team)
        for user in (cls.developer, cls.lead, cls.tester):
            user.set_password(PASSWORD)
            user.save()
        UserRole.objects.create(user=cls.developer, role=roles["DEVELOPER"])
        UserRole.objects.create(user=cls.lead, role=roles["TEAM_LEAD"])
        UserRole.objects.create(user=cls.tester, role=roles["TESTER"])

    def login(self, user):
        """Cached per test: the login endpoint is throttled at 5/min, and a
        fresh login per transition trips it."""
        if not hasattr(self, "_clients"):
            self._clients = {}
        if user.pk not in self._clients:
            client = Client()
            response = client.post(
                "/api/v1/auth/login/",
                data={"username": user.username, "password": PASSWORD},
                content_type="application/json",
            )
            self.assertEqual(response.status_code, 200, response.content)
            self._clients[user.pk] = client
        return self._clients[user.pk]

    def move(self, user, ticket, to_status, remarks="ok"):
        return self.login(user).post(
            f"/api/v1/tickets/{ticket.unique_id}/work-transition/",
            data={"to_status": to_status, "remarks": remarks},
            content_type="application/json",
        )

    def ticket(self, **kwargs):
        return make_ticket(
            ticket_type=TicketType.SERVICE_REQUEST, needs_review=False,
            status=TicketStatus.ASSIGNED, owner=self.developer, **kwargs,
        )

    def test_start_moves_an_assigned_ticket_into_progress(self):
        ticket = self.ticket()
        self.assertEqual(self.move(self.developer, ticket, "IN_PROGRESS").status_code, 200)
        ticket.refresh_from_db()
        self.assertEqual(ticket.status, TicketStatus.IN_PROGRESS)

    def test_full_path_to_testing_then_closed_by_a_tester(self):
        ticket = self.ticket()
        self.move(self.developer, ticket, "IN_PROGRESS")
        self.assertEqual(self.move(self.developer, ticket, "TESTING").status_code, 200)
        ticket.refresh_from_db()
        self.assertEqual(ticket.status, TicketStatus.TESTING)

        # QA may close the ticket, without gaining the separate bug-close API permission.
        self.assertEqual(self.move(self.developer, ticket, "CLOSED").status_code, 403)
        self.assertEqual(self.move(self.tester, ticket, "CLOSED").status_code, 200)
        ticket.refresh_from_db()
        self.assertEqual(ticket.status, TicketStatus.CLOSED)
        from apps.tickets.services.public_track_service import public_ticket_data
        self.assertTrue(public_ticket_data(ticket)["can_reopen"])
        self.assertEqual(self.move(self.developer, ticket, "IN_PROGRESS").status_code, 400)

    def test_on_hold_and_back_again(self):
        ticket = self.ticket()
        self.move(self.developer, ticket, "IN_PROGRESS")
        self.assertEqual(self.move(self.developer, ticket, "ON_HOLD").status_code, 200)
        self.assertEqual(self.move(self.developer, ticket, "IN_PROGRESS").status_code, 200)
        ticket.refresh_from_db()
        self.assertEqual(ticket.status, TicketStatus.IN_PROGRESS)

    def test_developer_can_only_have_one_active_ticket(self):
        first = self.ticket()
        second = self.ticket()
        self.assertEqual(self.move(self.developer, first, "IN_PROGRESS").status_code, 200)

        detail = self.login(self.developer).get(f"/api/v1/tickets/{second.unique_id}/")
        self.assertEqual(detail.status_code, 200, detail.content)
        self.assertEqual(detail.json()["data"]["active_work_conflict"]["id"], str(first.unique_id))

        blocked = self.move(self.developer, second, "IN_PROGRESS")
        self.assertEqual(blocked.status_code, 400, blocked.content)
        self.assertIn(first.reference, blocked.content.decode())

        self.assertEqual(self.move(self.developer, first, "PENDING").status_code, 200)
        self.assertEqual(self.move(self.developer, second, "IN_PROGRESS").status_code, 200)
        self.assertEqual(self.move(self.developer, first, "IN_PROGRESS").status_code, 400)

        self.assertEqual(self.move(self.developer, second, "ON_HOLD").status_code, 200)
        self.assertEqual(self.move(self.developer, first, "IN_PROGRESS").status_code, 200)

    def test_paused_ticket_cannot_change_to_other_pause_state(self):
        ticket = self.ticket()
        self.assertEqual(self.move(self.developer, ticket, "IN_PROGRESS").status_code, 200)
        self.assertEqual(self.move(self.developer, ticket, "PENDING").status_code, 200)
        self.assertEqual(self.move(self.developer, ticket, "ON_HOLD").status_code, 400)

    def test_linked_bug_status_counts_as_active_work(self):
        project, module, priority, severity = make_masters()
        bug = make_bug(
            bug_no="BUG-2610-9011", reporter=self.lead, owner=self.developer,
            project=project, module=module, priority=priority, severity=severity,
            status=BugStatus.IN_PROGRESS,
        )
        linked = make_ticket(
            ticket_type=TicketType.BUG, needs_review=False,
            status=TicketStatus.ASSIGNED, owner=self.developer, bug=bug,
            ticket_no="TKT-2610-9011",
        )
        other = self.ticket()

        blocked = self.move(self.developer, other, "IN_PROGRESS")
        self.assertEqual(blocked.status_code, 400, blocked.content)
        self.assertIn(linked.reference, blocked.content.decode())

    def test_direct_bug_start_obeys_ticket_work_limit(self):
        from apps.bugs.services.status_service import change_status
        from common.exceptions.domain import WorkflowValidationError

        active = self.ticket()
        self.assertEqual(self.move(self.developer, active, "IN_PROGRESS").status_code, 200)
        project, module, priority, severity = make_masters()
        bug = make_bug(
            bug_no="BUG-2610-9012", reporter=self.lead, owner=self.developer,
            project=project, module=module, priority=priority, severity=severity,
            status=BugStatus.ASSIGNED,
        )
        linked = make_ticket(
            ticket_type=TicketType.BUG, needs_review=False,
            status=TicketStatus.ASSIGNED, owner=self.developer, bug=bug,
            ticket_no="TKT-2610-9012",
        )

        with self.assertRaises(WorkflowValidationError):
            change_status(bug=bug, to_status=BugStatus.IN_PROGRESS, actor=self.developer)
        bug.refresh_from_db()
        self.assertEqual(bug.status, BugStatus.ASSIGNED)

        self.assertEqual(self.move(self.developer, active, "ON_HOLD").status_code, 200)
        change_status(bug=bug, to_status=BugStatus.IN_PROGRESS, actor=self.developer)
        bug.refresh_from_db()
        self.assertEqual(bug.status, BugStatus.IN_PROGRESS)
        self.assertEqual(linked.status, TicketStatus.ASSIGNED)

    def test_an_illegal_jump_is_refused(self):
        """Assigned straight to Closed skips the work and the verification."""
        ticket = self.ticket()
        response = self.move(self.lead, ticket, "CLOSED")
        self.assertEqual(response.status_code, 400, response.content)
        ticket.refresh_from_db()
        self.assertEqual(ticket.status, TicketStatus.ASSIGNED)

    @override_settings(TICKET_ACK_ENABLED=True)
    def test_closing_emails_the_requester(self):
        ticket = self.ticket(reported_by_email="requester@example.com")
        self.move(self.developer, ticket, "IN_PROGRESS")
        self.move(self.developer, ticket, "TESTING")
        django_mail.outbox.clear()

        self.assertEqual(self.move(self.tester, ticket, "CLOSED", "Verified").status_code, 200)
        self.deliver_notice(ticket, "CLOSED")
        self.assertEqual(len(django_mail.outbox), 1)
        message = django_mail.outbox[0]
        self.assertEqual(message.to, ["requester@example.com"])
        self.assertIn("closed", message.body.lower())
        self.assertIn("within 2 days (48 hours)", message.body)

    @override_settings(TICKET_ACK_ENABLED=True)
    def test_no_closure_mail_when_no_requester_address(self):
        ticket = self.ticket(reported_by_email="")
        self.move(self.developer, ticket, "IN_PROGRESS")
        self.move(self.developer, ticket, "TESTING")
        django_mail.outbox.clear()

        self.assertEqual(self.move(self.tester, ticket, "CLOSED").status_code, 200)
        self.assertEqual(django_mail.outbox, [])

    @override_settings(TICKET_ACK_ENABLED=True, PUBLIC_APP_URL="https://tracker.example.com")
    def test_first_assignment_sends_number_and_tracking_link_once(self):
        ticket = make_ticket(
            ticket_type=TicketType.SERVICE_REQUEST, needs_review=False,
            status=TicketStatus.NEW, owner=None,
        )
        client = self.login(self.lead)
        url = f"/api/v1/tickets/{ticket.unique_id}/assign/"
        payload = {"owner": str(self.developer.unique_id)}
        django_mail.outbox.clear()
        response = client.post(url, data=payload, content_type="application/json")
        self.assertEqual(response.status_code, 200, response.content)
        ticket.refresh_from_db()
        self.assertTrue(ticket.ticket_no)
        self.deliver_notice(ticket, "ASSIGNED")
        self.assertEqual(len(django_mail.outbox), 1)
        self.assertIn(ticket.ticket_no, django_mail.outbox[0].subject)
        self.assertIn("https://tracker.example.com/track", django_mail.outbox[0].body)
        self.assertIn(f"Enter ticket number {ticket.ticket_no}", django_mail.outbox[0].body)
        self.assertIn("message our support team in the Chat tab", django_mail.outbox[0].body)
        self.assertNotIn("when replying", django_mail.outbox[0].body)
        self.assertEqual(client.post(url, data=payload, content_type="application/json").status_code, 200)
        self.assertEqual(len(django_mail.outbox), 1)

    @override_settings(TICKET_ACK_ENABLED=True, PUBLIC_APP_URL="https://tracker.example.com")
    def test_review_with_owner_sends_assignment_notice(self):
        ticket = make_ticket(ticket_type=TicketType.UNKNOWN, needs_review=True)
        django_mail.outbox.clear()
        response = self.login(self.lead).post(
            f"/api/v1/tickets/{ticket.unique_id}/review/",
            data={"ticket_type": "SERVICE_REQUEST", "owner": str(self.developer.unique_id)},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200, response.content)
        ticket.refresh_from_db()
        self.deliver_notice(ticket, "ASSIGNED")
        self.assertEqual(len(django_mail.outbox), 1)
        self.assertIn(ticket.ticket_no, django_mail.outbox[0].subject)
        self.assertIn("https://tracker.example.com/track", django_mail.outbox[0].body)

    @override_settings(TICKET_ACK_ENABLED=True)
    def test_manual_intake_sends_reference_acknowledgement(self):
        django_mail.outbox.clear()
        response = self.login(self.lead).post(
            "/api/v1/tickets/",
            data={
                "ticket_type": "SERVICE_REQUEST", "title": "Help with access",
                "description": "Please grant access.", "source": "EMAIL",
                "reported_by_email": "requester@example.com",
            },
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 201, response.content)
        self.deliver_notice(OutboundMail.objects.get(kind="ACK").ticket, "ACK")
        self.assertEqual(len(django_mail.outbox), 1)
        self.assertIn("[REF-", django_mail.outbox[0].subject)
        self.assertIn("REF-", django_mail.outbox[0].body)

    @override_settings(TICKET_ACK_ENABLED=True)
    def test_tester_closes_linked_bug_and_emails_requester(self):
        project, module, priority, severity = make_masters()
        bug = make_bug(
            bug_no="BUG-2609-9010", reporter=self.lead, owner=self.developer,
            project=project, module=module, priority=priority,
            severity=severity, status=BugStatus.TESTING,
            root_cause="Missing access grant", resolution="Granted access",
            latest_remarks="Ready for verification",
        )
        ticket = make_ticket(
            ticket_type=TicketType.BUG, needs_review=False,
            status=TicketStatus.TESTING, owner=self.developer, bug=bug,
            ticket_no="TKT-2609-9010", reported_by_email="requester@example.com",
        )
        django_mail.outbox.clear()
        self.assertEqual(self.move(self.developer, ticket, "IN_PROGRESS", "Found another issue").status_code, 200)
        self.assertEqual(self.move(self.developer, ticket, "TESTING", "Rechecked the fix").status_code, 200)
        self.assertEqual(self.move(self.developer, ticket, "CLOSED", "Verified").status_code, 403)
        response = self.move(self.tester, ticket, "CLOSED", "Verified")
        self.assertEqual(response.status_code, 200, response.content)
        ticket.refresh_from_db()
        bug.refresh_from_db()
        self.assertEqual(ticket.status, TicketStatus.CLOSED)
        self.assertEqual(bug.status, BugStatus.CLOSED)
        self.assertEqual(bug.verification_result, VerificationResult.PASSED)
        from apps.tickets.services.public_track_service import public_ticket_data
        self.assertTrue(public_ticket_data(ticket)["can_reopen"])
        self.deliver_notice(ticket, "CLOSED")
        self.assertEqual(len(django_mail.outbox), 1)
        self.assertIn(ticket.ticket_no, django_mail.outbox[0].subject)
        self.assertEqual(self.move(self.developer, ticket, "IN_PROGRESS", "Retake again").status_code, 400)

    def test_stale_testing_ticket_does_not_expose_closed_bug_to_tester(self):
        project, module, priority, severity = make_masters()
        bug = make_bug(
            bug_no="BUG-2609-9012", reporter=self.lead, owner=self.developer,
            project=project, module=module, priority=priority,
            severity=severity, status=BugStatus.CLOSED,
        )
        ticket = make_ticket(
            ticket_type=TicketType.BUG, needs_review=False,
            status=TicketStatus.TESTING, owner=self.developer, bug=bug,
        )
        response = self.login(self.tester).get(f"/api/v1/tickets/{ticket.unique_id}/")
        self.assertEqual(response.status_code, 404)

    def test_reopened_service_ticket_waits_for_tester_return_decision(self):
        ticket = make_ticket(
            ticket_type=TicketType.SERVICE_REQUEST, needs_review=False,
            status=TicketStatus.REOPENED, owner=self.developer,
            ticket_no="TKT-2609-9013",
        )
        self.assertEqual(self.move(self.developer, ticket, "IN_PROGRESS", "Retake").status_code, 403)
        response = self.login(self.tester).get("/api/v1/tickets/?verification_queue=true")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertIn(ticket.ticket_no, [row["ticket_no"] for row in response.json()["data"]["results"]])
        self.assertEqual(self.move(self.tester, ticket, "IN_PROGRESS", "Issue persists").status_code, 200)
        ticket.refresh_from_db()
        self.assertEqual(ticket.status, TicketStatus.IN_PROGRESS)

    @override_settings(TICKET_ACK_ENABLED=True)
    def test_reopened_service_ticket_can_be_verified_and_closed(self):
        ticket = make_ticket(
            ticket_type=TicketType.SERVICE_REQUEST, needs_review=False,
            status=TicketStatus.REOPENED, owner=self.developer,
            ticket_no="TKT-2609-9014", reported_by_email="requester@example.com",
        )
        django_mail.outbox.clear()
        self.assertEqual(self.move(self.tester, ticket, "CLOSED", "Fix is still valid").status_code, 200)
        ticket.refresh_from_db()
        self.assertEqual(ticket.status, TicketStatus.CLOSED)
        self.deliver_notice(ticket, "CLOSED")
        self.assertEqual(len(django_mail.outbox), 1)

    @override_settings(TICKET_ACK_ENABLED=True)
    def test_reopened_bug_requires_tester_to_reverify_before_closure(self):
        project, module, priority, severity = make_masters()
        bug = make_bug(
            bug_no="BUG-2609-9015", reporter=self.lead, owner=self.developer,
            project=project, module=module, priority=priority,
            severity=severity, status=BugStatus.REOPENED,
            root_cause="Missing access grant", resolution="Granted access",
        )
        ticket = make_ticket(
            ticket_type=TicketType.BUG, needs_review=False,
            status=TicketStatus.REOPENED, owner=self.developer, bug=bug,
            ticket_no="TKT-2609-9015", reported_by_email="requester@example.com",
        )
        queue = self.login(self.tester).get("/api/v1/tickets/?verification_queue=true")
        self.assertEqual(queue.status_code, 200, queue.content)
        self.assertIn(ticket.ticket_no, [row["ticket_no"] for row in queue.json()["data"]["results"]])
        developer = self.login(self.developer)
        bypass = developer.post(
            f"/api/v1/bugs/{bug.unique_id}/resolve/",
            data={"root_cause": bug.root_cause, "resolution": bug.resolution},
            content_type="application/json",
        )
        self.assertNotEqual(bypass.status_code, 200)
        self.assertEqual(self.move(self.developer, ticket, "IN_PROGRESS", "Retake").status_code, 403)
        django_mail.outbox.clear()
        self.assertEqual(self.move(self.tester, ticket, "CLOSED", "Reverified").status_code, 200)
        ticket.refresh_from_db()
        bug.refresh_from_db()
        self.assertEqual(ticket.status, TicketStatus.CLOSED)
        self.assertEqual(bug.status, BugStatus.CLOSED)
        self.deliver_notice(ticket, "CLOSED")
        self.assertEqual(len(django_mail.outbox), 1)

    @override_settings(TICKET_ACK_ENABLED=True)
    def test_lead_closing_bug_directly_updates_ticket_and_emails_requester(self):
        project, module, priority, severity = make_masters()
        bug = make_bug(
            bug_no="BUG-2609-9011", reporter=self.lead, owner=self.developer,
            project=project, module=module, priority=priority,
            severity=severity, status=BugStatus.RESOLVED,
            root_cause="Missing access grant", resolution="Granted access",
            verification_result=VerificationResult.PASSED,
        )
        ticket = make_ticket(
            ticket_type=TicketType.BUG, needs_review=False,
            status=TicketStatus.TESTING, owner=self.developer, bug=bug,
            ticket_no="TKT-2609-9011", reported_by_email="requester@example.com",
        )
        django_mail.outbox.clear()
        response = self.login(self.lead).post(
            f"/api/v1/bugs/{bug.unique_id}/close/",
            data={"closure_remarks": "Verified by QA"},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200, response.content)
        ticket.refresh_from_db()
        self.assertEqual(ticket.status, TicketStatus.CLOSED)
        self.deliver_notice(ticket, "CLOSED")
        self.assertEqual(len(django_mail.outbox), 1)
        self.assertIn(ticket.ticket_no, django_mail.outbox[0].subject)


@override_settings(REST_FRAMEWORK={"DEFAULT_THROTTLE_RATES": {}})
class PublicTicketLookupTests(TestCase):
    """Unauthenticated status lookup. A wrong pair must reveal nothing."""

    def setUp(self):
        self.client = Client()
        self.ticket = make_ticket(
            ticket_type=TicketType.SERVICE_REQUEST, needs_review=False,
            status=TicketStatus.IN_PROGRESS,
            ticket_no="TKT-2609-9001",
            reported_by_email="requester@example.com",
            title="Printer will not start",
        )

    def lookup(self, ticket_no, email):
        return self.client.post(
            "/api/v1/tickets/public/lookup/",
            data={"ticket_no": ticket_no, "email": email},
            content_type="application/json",
        )

    def test_correct_pair_returns_the_status(self):
        response = self.lookup("TKT-2609-9001", "requester@example.com")
        self.assertEqual(response.status_code, 200, response.content)
        data = response.json()["data"]
        self.assertTrue(data["found"])
        self.assertEqual(data["status_label"], "In progress")
        self.assertEqual(data["ticket_no"], "TKT-2609-9001")

    def test_lookup_needs_no_login(self):
        self.assertEqual(
            self.lookup("TKT-2609-9001", "requester@example.com").status_code, 200)

    def test_right_number_with_the_wrong_email_reveals_nothing(self):
        """The whole point of asking for both: a guessed number is not enough."""
        response = self.lookup("TKT-2609-9001", "stranger@example.com")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["data"], {"found": False})

    def test_unknown_number_answers_the_same_way(self):
        response = self.lookup("TKT-2609-0000", "requester@example.com")
        self.assertEqual(response.json()["data"], {"found": False})

    def test_response_never_carries_internal_detail(self):
        data = self.lookup("TKT-2609-9001", "requester@example.com").json()["data"]
        for leaked in ("description", "owner", "remarks", "reported_by_name",
                       "classification_reason", "updates"):
            self.assertNotIn(leaked, data)

    def test_case_and_padding_are_forgiven(self):
        response = self.lookup("  tkt-2609-9001  ", "Requester@Example.com")
        self.assertTrue(response.json()["data"]["found"])
