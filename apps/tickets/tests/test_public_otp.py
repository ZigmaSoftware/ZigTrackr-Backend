"""The passcode is what stands between a guessed ticket number and a stranger's
support thread, so these tests pin the properties that make it worth having:
the request step reveals nothing, and no failure path reaches a session cookie.
"""

import json
from datetime import timedelta
from unittest import mock

from django.core.cache import cache
from django.test import Client, TestCase
from django.utils import timezone

from apps.bugs.tests.factories import make_user
from apps.tickets.constants import TicketStatus, TicketType
from apps.tickets.models import OutboundMail, TicketAccessOtp
from apps.tickets.services.ticket_otp_service import MAX_ATTEMPTS
from apps.tickets.tests.factories import make_ticket

REQUEST_URL = "/api/v1/tickets/public/track/request-otp/"
VERIFY_URL = "/api/v1/tickets/public/track/verify-otp/"
COOKIE = "zigtrackr_public_ticket"
EMAIL = "sender@example.com"
CODE = "123456"


class PublicOtpTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = make_user("otp_owner")

    def setUp(self):
        cache.clear()
        self.client = Client(enforce_csrf_checks=True)
        self.ticket = make_ticket(
            ticket_type=TicketType.SERVICE_REQUEST, needs_review=False,
            ticket_no="TKT-2610-0077", owner=self.owner,
            status=TicketStatus.IN_PROGRESS, reported_by_email=EMAIL,
        )
        self.client.get("/api/v1/auth/csrf/")

    def tearDown(self):
        cache.clear()

    def post(self, path, payload):
        return self.client.post(
            path, data=json.dumps(payload), content_type="application/json",
            HTTP_X_CSRFTOKEN=self.client.cookies["csrftoken"].value,
        )

    def request_otp(self, email=EMAIL, code=CODE):
        with mock.patch(
            "apps.tickets.services.ticket_otp_service.generate_code", return_value=code,
        ):
            return self.post(REQUEST_URL, {"email": email})

    def verify(self, code=CODE, email=EMAIL):
        return self.post(VERIFY_URL, {"email": email, "code": code})

    def has_session(self):
        morsel = self.client.cookies.get(COOKIE)
        return bool(morsel and morsel.value)

    # ---- the request step must not be an oracle ----

    def test_unknown_address_is_indistinguishable_from_a_known_one(self):
        good = self.request_otp()
        self.client.cookies.pop(COOKIE, None)
        bad = self.request_otp(email="stranger@example.com")

        self.assertEqual(good.status_code, bad.status_code)
        self.assertEqual(good.json()["data"], bad.json()["data"])
        self.assertEqual(good.json()["message"], bad.json()["message"])

    def test_wrong_email_sends_nothing_and_stores_nothing(self):
        self.request_otp(email="stranger@example.com")
        self.assertEqual(TicketAccessOtp.objects.count(), 0)
        self.assertEqual(OutboundMail.objects.filter(kind="OTP").count(), 0)

    def test_request_never_issues_a_session(self):
        self.request_otp()
        self.assertFalse(self.has_session())

    # ---- the code itself ----

    def test_matching_pair_queues_mail_and_stores_only_a_hash(self):
        self.request_otp()
        otp = TicketAccessOtp.objects.get()
        self.assertNotIn(CODE, otp.code_hash)
        self.assertEqual(otp.email, EMAIL)

        job = OutboundMail.objects.get(kind="OTP")
        self.assertEqual(job.recipient, EMAIL)
        self.assertIn(CODE, job.body)
        # Each digit is wrapped in its own span for letter-spacing, so the
        # code never appears as one literal string in the HTML.
        import re
        rendered = "".join(re.findall(r">(\d)</span>", job.html_body))
        self.assertIn(CODE, rendered)
        # The code must NOT be in the subject: a bare number there is a known
        # spam signal, and the code no longer belongs to any one ticket.
        self.assertNotIn(CODE, job.subject)
        # Nor may the mail name a ticket -- sign-in is by address now, and the
        # code opens every ticket that address owns.
        self.assertNotIn(self.ticket.ticket_no, job.body)
        self.assertNotIn(self.ticket.ticket_no, job.html_body)
        # Carries the code's own deadline so a late retry cannot deliver it.
        self.assertEqual(job.expires_at, otp.expires_at)

    def test_correct_code_opens_the_session(self):
        self.request_otp()
        response = self.verify()
        self.assertEqual(response.status_code, 200, response.content)
        data = response.json()["data"]
        self.assertEqual(data["email"], EMAIL)
        self.assertIn(
            self.ticket.ticket_no, [row["ticket_no"] for row in data["tickets"]],
        )
        self.assertTrue(self.has_session())

    def test_expired_code_is_rejected(self):
        self.request_otp()
        TicketAccessOtp.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
        response = self.verify()
        self.assertEqual(response.status_code, 400)
        self.assertFalse(self.has_session())

    def test_code_cannot_be_replayed(self):
        self.request_otp()
        self.assertEqual(self.verify().status_code, 200)
        self.client.cookies.pop(COOKIE, None)
        self.assertEqual(self.verify().status_code, 400)

    def test_resend_supersedes_the_previous_code(self):
        self.request_otp(code="111111")
        self.request_otp(code="222222")
        self.assertEqual(self.verify(code="111111").status_code, 400)
        self.assertEqual(self.verify(code="222222").status_code, 200)

    # ---- attempt limiting ----

    def test_attempts_are_capped_even_with_the_right_code(self):
        self.request_otp()
        for _ in range(MAX_ATTEMPTS):
            self.assertEqual(self.verify(code="000000").status_code, 400)
        # The counter, not the code, is now what blocks entry.
        self.assertEqual(self.verify(code=CODE).status_code, 400)
        self.assertFalse(self.has_session())

    def test_wrong_code_increments_attempts_and_sets_no_cookie(self):
        self.request_otp()
        response = self.verify(code="000000")
        self.assertEqual(response.status_code, 400)
        self.assertIn("code", response.json()["errors"])
        self.assertEqual(TicketAccessOtp.objects.get().attempts, 1)
        self.assertFalse(self.has_session())

    def test_code_is_scoped_to_the_address_it_was_issued_for(self):
        """A code issued for one address must not verify another."""
        make_ticket(
            ticket_type=TicketType.SERVICE_REQUEST, needs_review=False,
            ticket_no="TKT-2610-0078", owner=self.owner,
            status=TicketStatus.IN_PROGRESS, reported_by_email="other@example.com",
        )
        self.request_otp()  # issued for EMAIL
        response = self.verify(email="other@example.com")
        self.assertEqual(response.status_code, 400)
        self.assertFalse(self.has_session())

    def test_session_covers_every_ticket_the_address_owns(self):
        second = make_ticket(
            ticket_type=TicketType.SERVICE_REQUEST, needs_review=False,
            ticket_no="TKT-2610-0079", owner=self.owner,
            status=TicketStatus.IN_PROGRESS, reported_by_email=EMAIL,
        )
        self.request_otp()
        data = self.verify().json()["data"]
        numbers = [row["ticket_no"] for row in data["tickets"]]
        self.assertIn(self.ticket.ticket_no, numbers)
        self.assertIn(second.ticket_no, numbers)
        # One sign-in reaches both, with no further verification.
        for number in (self.ticket.ticket_no, second.ticket_no):
            got = self.client.get(
                "/api/v1/tickets/public/track/ticket/", {"ticket_no": number},
            )
            self.assertEqual(got.status_code, 200, number)

    def test_a_ticket_owned_by_someone_else_is_not_reachable(self):
        """The signed-in address is the gate, not the number in the request."""
        stranger = make_ticket(
            ticket_type=TicketType.SERVICE_REQUEST, needs_review=False,
            ticket_no="TKT-2610-0080", owner=self.owner,
            status=TicketStatus.IN_PROGRESS, reported_by_email="stranger@example.com",
        )
        self.request_otp()
        self.verify()
        got = self.client.get(
            "/api/v1/tickets/public/track/ticket/", {"ticket_no": stranger.ticket_no},
        )
        self.assertEqual(got.status_code, 404)
