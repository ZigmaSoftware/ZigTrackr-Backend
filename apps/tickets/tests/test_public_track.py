"""Public tracking must disclose nothing until ticket and email match."""

from datetime import timedelta

from django.core import mail
from django.test import Client, TestCase
from django.test import override_settings
from unittest import mock

from django.core.cache import cache
from django.utils import timezone

from apps.bugs.tests.factories import make_user
from apps.tickets.constants import TicketStatus, TicketType
from apps.tickets.models import TicketActivity, TicketChatMessage, TicketReopenHistory
from apps.tickets.tests.factories import make_ticket


class PublicTrackTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = make_user("track_owner")

    def setUp(self):
        cache.clear()
        self.client = Client(enforce_csrf_checks=True)
        self.ticket = make_ticket(
            ticket_type=TicketType.SERVICE_REQUEST, needs_review=False,
            ticket_no="TKT-2609-0011", owner=self.owner,
            status=TicketStatus.IN_PROGRESS, reported_by_email="sender@example.com",
        )
        self.client.get("/api/v1/auth/csrf/")

    def tearDown(self):
        cache.clear()

    def post(self, path, payload):
        import json

        return self.client.post(
            path, data=json.dumps(payload), content_type="application/json",
            HTTP_X_CSRFTOKEN=self.client.cookies["csrftoken"].value,
        )

    def verify(self, email="sender@example.com"):
        """Drive the two-step passcode flow and return the final response.

        Other tests only need an authenticated session, so the OTP round trip
        is hidden here. The code is read from the database rather than the
        outbox: only its hash is stored, so the plain value is captured at
        generation time by patching the generator.
        """
        with mock.patch(
            "apps.tickets.services.ticket_otp_service.generate_code",
            return_value="123456",
        ):
            self.post("/api/v1/tickets/public/track/request-otp/", {"email": email})
        return self.post("/api/v1/tickets/public/track/verify-otp/", {
            "email": email, "code": "123456",
        })

    def detail(self, email="sender@example.com"):
        """Sign in, then read the ticket's own payload.

        verify-otp now returns the LIST of a verified address's tickets, so
        tests asserting on one ticket fetch its detail explicitly.
        """
        self.verify(email)
        return self.client.get(
            "/api/v1/tickets/public/track/ticket/",
            {"ticket_no": self.ticket.ticket_no},
        )

    def close_ticket(self, closed_at=None):
        self.ticket.status = TicketStatus.CLOSED
        self.ticket.save(update_fields=["status"])
        TicketActivity.objects.create(
            ticket=self.ticket, event_type="TICKET_CLOSED", title="Ticket Closed",
            occurred_at=closed_at or timezone.now(),
        )

    def test_verify_lists_the_addresss_tickets_and_issues_scoped_cookie(self):
        # An address that owns nothing fails at the code step: no passcode was
        # ever issued for it, so there is nothing to verify.
        bad = self.verify("wrong@example.com")
        self.assertEqual(bad.status_code, 400)
        self.assertFalse(self.client.cookies.get("zigtrackr_public_ticket", ""))
        self.assertEqual(
            self.client.get("/api/v1/tickets/public/track/activity/").status_code, 404,
        )

        good = self.verify()
        self.assertEqual(good.status_code, 200, good.content)
        data = good.json()["data"]
        # Sign-in now returns every ticket the verified address owns.
        self.assertEqual(data["email"], "sender@example.com")
        self.assertIn(
            self.ticket.ticket_no, [row["ticket_no"] for row in data["tickets"]],
        )
        cookie = self.client.cookies["zigtrackr_public_ticket"]
        self.assertTrue(cookie["httponly"])
        self.assertEqual(cookie["path"], "/api/v1/tickets/public/track/")

    def test_verify_rejects_cross_site_post_without_csrf(self):
        import json

        response = self.client.post(
            "/api/v1/tickets/public/track/request-otp/",
            data=json.dumps({"email": "sender@example.com"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 403)

    def test_public_activity_excludes_internal_only_entries(self):
        TicketActivity.objects.create(
            ticket=self.ticket, event_type="INTERNAL", title="Private note",
            description="Secret diagnostic", public_description="",
            occurred_at=self.ticket.created_at,
        )
        TicketActivity.objects.create(
            ticket=self.ticket, event_type="WORK_STARTED", title="Work started",
            description="Internal root cause", public_description="Your request is being worked on.",
            occurred_at=self.ticket.created_at,
        )
        self.verify()
        response = self.client.get("/api/v1/tickets/public/track/activity/")
        self.assertEqual(response.status_code, 200, response.content)
        rows = response.json()["data"]
        self.assertEqual(len(rows), 2)
        self.assertNotIn("Internal root cause", str(rows))
        self.assertNotIn("Secret diagnostic", str(rows))

    def test_chat_persists_and_becomes_read_only_in_testing(self):
        self.verify()
        sent = self.post("/api/v1/tickets/public/track/chat/messages/", {"message": "Any update?"})
        self.assertEqual(sent.status_code, 200, sent.content)
        self.assertEqual(TicketChatMessage.objects.filter(ticket=self.ticket).count(), 1)
        self.assertEqual(
            self.client.get("/api/v1/tickets/public/track/chat/messages/").json()["data"][0]["message_text"],
            "Any update?",
        )
        self.ticket.status = TicketStatus.TESTING
        self.ticket.save(update_fields=["status"])
        blocked = self.post("/api/v1/tickets/public/track/chat/messages/", {"message": "Still there?"})
        self.assertEqual(blocked.status_code, 400, blocked.content)

    def test_chat_reply_edit_react_star_pin_and_delete(self):
        self.verify()
        base = "/api/v1/tickets/public/track/chat/messages/"
        first = self.post(base, {"message": "Original"}).json()["data"]
        reply = self.post(base, {"message": "Follow up", "reply_to": first["id"]}).json()["data"]
        self.assertEqual(reply["reply_to"]["id"], first["id"])
        action = f"{base}{first['id']}/"

        edited = self.post(action, {"action": "edit", "message": "Corrected"})
        self.assertEqual(edited.status_code, 200, edited.content)
        self.assertEqual(edited.json()["data"]["message_text"], "Corrected")
        self.assertIsNotNone(edited.json()["data"]["edited_at"])
        self.assertEqual(TicketChatMessage.objects.get(unique_id=first["id"]).revisions.count(), 1)

        reacted = self.post(action, {"action": "react", "emoji": "👍"}).json()["data"]
        self.assertEqual(reacted["reactions"], [{"emoji": "👍", "count": 1, "mine": True}])
        self.assertEqual(self.post(action, {"action": "react", "emoji": "👍"}).json()["data"]["reactions"], [])
        self.assertTrue(self.post(action, {"action": "star"}).json()["data"]["is_starred"])
        self.assertTrue(self.post(action, {"action": "pin"}).json()["data"]["is_pinned"])

        deleted = self.post(action, {"action": "delete"})
        self.assertEqual(deleted.status_code, 200, deleted.content)
        self.assertTrue(deleted.json()["data"]["is_deleted"])
        self.assertEqual(deleted.json()["data"]["message_text"], "")
        history = self.client.get(base).json()["data"]
        self.assertEqual(len(history), 2)
        self.assertTrue(history[1]["reply_to"]["is_deleted"])
        self.assertEqual(history[1]["reply_to"]["message_text"], "")

    def test_public_chat_action_requires_verified_ticket_and_owner(self):
        staff = TicketChatMessage.objects.create(
            ticket=self.ticket, sender_type="STAFF", sender_user=self.owner,
            sender_display_name="Support", message_text="Staff message",
        )
        action = f"/api/v1/tickets/public/track/chat/messages/{staff.unique_id}/"
        self.assertEqual(self.post(action, {"action": "edit", "message": "Changed"}).status_code, 404)
        self.verify()
        self.assertEqual(self.post(action, {"action": "delete"}).status_code, 403)
        self.assertEqual(self.post(action, {"action": "react", "emoji": "🔥"}).status_code, 400)

    def test_public_read_receipt_locks_staff_edit(self):
        from apps.tickets.services.chat_service import change_message
        from common.exceptions.domain import WorkflowValidationError

        staff = TicketChatMessage.objects.create(
            ticket=self.ticket, sender_type="STAFF", sender_user=self.owner,
            sender_display_name="Support", message_text="We are checking",
        )
        receipt_path = "/api/v1/tickets/public/track/chat/receipts/"
        self.assertEqual(self.post(receipt_path, {
            "message_ids": [str(staff.unique_id)], "status": "read",
        }).status_code, 404)
        self.verify()
        response = self.post(receipt_path, {
            "message_ids": [str(staff.unique_id)], "status": "read",
        })
        self.assertEqual(response.status_code, 200, response.content)
        row = response.json()["data"][0]
        self.assertIsNotNone(row["delivered_at"])
        self.assertIsNotNone(row["read_at"])
        self.assertFalse(row["can_edit"])
        with self.assertRaises(WorkflowValidationError):
            change_message(ticket=self.ticket, message_id=staff.unique_id,
                           action="edit", user=self.owner, text="Changed")

        requester = self.post("/api/v1/tickets/public/track/chat/messages/", {
            "message": "My message",
        }).json()["data"]
        own = self.post(receipt_path, {
            "message_ids": [requester["id"]], "status": "read",
        })
        self.assertEqual(own.json()["data"], [])

    def test_reopen_requires_closed_status_and_prevents_duplicate(self):
        self.close_ticket()
        self.verify()
        reopened = self.post("/api/v1/tickets/public/track/reopen/", {"reason": "Issue persists."})
        self.assertEqual(reopened.status_code, 200, reopened.content)
        self.ticket.refresh_from_db()
        self.assertEqual(self.ticket.status, TicketStatus.REOPENED)
        self.assertEqual(TicketReopenHistory.objects.filter(ticket=self.ticket).count(), 1)
        duplicate = self.post("/api/v1/tickets/public/track/reopen/", {"reason": "Again."})
        self.assertEqual(duplicate.status_code, 400, duplicate.content)
        self.assertEqual(TicketReopenHistory.objects.filter(ticket=self.ticket).count(), 1)

    def test_resolved_ticket_can_be_confirmed_fixed_once_and_cannot_reopen(self):
        self.close_ticket()
        data = self.detail().json()["data"]
        self.assertEqual(data["public_stage"], "RESOLVED")
        self.assertTrue(data["can_confirm_close"])
        self.assertIsNotNone(data["auto_closes_at"])
        path = "/api/v1/tickets/public/track/close/"
        for _ in range(2):
            response = self.post(path, {"ticket_no": self.ticket.ticket_no})
            self.assertEqual(response.status_code, 200, response.content)
            data = response.json()["data"]
            self.assertEqual(data["public_stage"], "CLOSED")
            self.assertFalse(data["can_reopen"])
            self.assertFalse(data["can_confirm_close"])
        self.assertEqual(self.ticket.activities.filter(event_type="TICKET_REQUESTER_CLOSED").count(), 1)
        response = self.post("/api/v1/tickets/public/track/reopen/", {"reason": "Again"})
        self.assertEqual(response.status_code, 400)

    def test_confirmation_requires_verified_ownership_and_csrf(self):
        self.close_ticket()
        path = "/api/v1/tickets/public/track/close/"
        self.assertEqual(self.post(path, {"ticket_no": self.ticket.ticket_no}).status_code, 404)
        self.verify()
        other = make_ticket(ticket_no="TKT-2610-0999", reported_by_email="other@example.com")
        self.assertEqual(self.post(path, {"ticket_no": other.ticket_no}).status_code, 404)
        response = self.client.post(path, data='{}', content_type="application/json")
        self.assertEqual(response.status_code, 403)
        self.assertFalse(self.ticket.activities.filter(event_type="TICKET_REQUESTER_CLOSED").exists())

    def test_active_ticket_cannot_be_closed_by_requester(self):
        self.verify()
        response = self.post("/api/v1/tickets/public/track/close/", {"ticket_no": self.ticket.ticket_no})
        self.assertEqual(response.status_code, 400)
        self.ticket.refresh_from_db()
        self.assertEqual(self.ticket.status, TicketStatus.IN_PROGRESS)

    def test_automatic_closure_finishes_at_exact_two_day_deadline(self):
        now = timezone.now()
        self.close_ticket(now - timedelta(days=2))
        self.verify()
        with mock.patch("apps.tickets.services.public_track_service.timezone.now", return_value=now):
            data = self.client.get("/api/v1/tickets/public/track/ticket/").json()["data"]
        self.assertEqual(data["public_stage"], "CLOSED")
        self.assertFalse(data["can_confirm_close"])
        self.assertFalse(data["can_reopen"])

    def test_confirmation_of_an_old_cycle_does_not_close_a_new_resolution(self):
        self.close_ticket(timezone.now() - timedelta(hours=4))
        TicketActivity.objects.create(
            ticket=self.ticket, event_type="TICKET_REQUESTER_CLOSED", title="Confirmed",
            occurred_at=timezone.now() - timedelta(hours=3),
        )
        self.close_ticket(timezone.now() - timedelta(hours=1))
        data = self.detail().json()["data"]
        self.assertTrue(data["can_reopen"])
        self.assertTrue(data["can_confirm_close"])

    def test_signout_expires_the_tracking_cookie_at_its_original_path(self):
        self.verify()
        response = self.post("/api/v1/tickets/public/track/signout/", {})
        self.assertEqual(response.status_code, 200)
        cookie = response.cookies["zigtrackr_public_ticket"]
        self.assertEqual(cookie["max-age"], 0)
        self.assertEqual(cookie["path"], "/api/v1/tickets/public/track/")
        self.assertEqual(self.client.get("/api/v1/tickets/public/track/tickets/").status_code, 404)

    def test_list_closure_statuses_and_unread_counts_use_one_query(self):
        from apps.tickets.services.public_track_service import public_ticket_list

        self.close_ticket()
        for _ in range(3):
            TicketActivity.objects.create(ticket=self.ticket, event_type="WORK_STARTED", title="Started", occurred_at=timezone.now())
        TicketChatMessage.objects.create(ticket=self.ticket, sender_type="STAFF", sender_display_name="Support", message_text="Done")
        with self.assertNumQueries(1):
            rows = public_ticket_list("sender@example.com")
        self.assertEqual(rows[0]["public_stage"], "RESOLVED")
        self.assertEqual(rows[0]["unread_count"], 1)

    def test_resent_status_matches_the_list_and_detail_progress(self):
        self.ticket.status = TicketStatus.ASSIGNED
        self.ticket.save(update_fields=["status"])
        now = timezone.now()
        for i, event in enumerate(("WORK_STARTED", "TICKET_RECTIFIED", "TICKET_RETURNED_TO_DEVELOPER")):
            TicketActivity.objects.create(
                ticket=self.ticket, event_type=event, title=event,
                occurred_at=now - timedelta(minutes=3 - i),
            )
        data = self.detail().json()["data"]
        self.assertEqual(data["public_stage"], "RESENT")
        self.assertEqual(data["progress"][0]["code"], "RESENT")
        rows = self.client.get("/api/v1/tickets/public/track/tickets/").json()["data"]["tickets"]
        self.assertEqual(rows[0]["public_stage"], "RESENT")
        self.assertEqual(rows[0]["status_label"], "Resent")

    def test_bug_progress_uses_typed_history_without_exposing_private_remarks(self):
        from apps.bugs.models import BugStatusHistory
        from apps.bugs.tests.factories import make_bug, make_masters

        project, module, priority, severity = make_masters()
        bug = make_bug(
            bug_no="BUG-2610-0101", reporter=self.owner, project=project,
            module=module, priority=priority, severity=severity,
            owner=self.owner, status="TESTING",
        )
        self.ticket.bug = bug
        self.ticket.save(update_fields=["bug"])
        previous = "NEW"
        for status in ("ASSIGNED", "IN_PROGRESS", "TESTING", "ASSIGNED", "IN_PROGRESS", "TESTING"):
            BugStatusHistory.objects.create(
                bug=bug, from_status=previous, to_status=status,
                changed_by=self.owner, remarks="Private diagnostic details",
            )
            previous = status
        data = self.detail().json()["data"]
        self.assertEqual(data["progress"][0]["code"], "RESENT")
        self.assertEqual(data["progress"][-2]["state"], "current")
        self.assertNotIn("Private diagnostic details", str(data))

    def test_reopen_expires_two_days_after_closure(self):
        self.close_ticket(timezone.now() - timedelta(days=2, seconds=1))
        self.assertFalse(self.detail().json()["data"]["can_reopen"])
        response = self.post("/api/v1/tickets/public/track/reopen/", {"reason": "Still broken."})
        self.assertEqual(response.status_code, 400, response.content)
        self.ticket.refresh_from_db()
        self.assertEqual(self.ticket.status, TicketStatus.CLOSED)
        self.assertFalse(TicketReopenHistory.objects.filter(ticket=self.ticket).exists())

    def test_reopen_window_uses_latest_closure_not_ticket_update_time(self):
        self.close_ticket(timezone.now() - timedelta(days=3))
        self.ticket.save(update_fields=["updated_at"])
        self.assertFalse(self.detail().json()["data"]["can_reopen"])

        TicketActivity.objects.create(
            ticket=self.ticket, event_type="TICKET_CLOSED", title="Ticket Closed",
            occurred_at=timezone.now() - timedelta(hours=47),
        )
        self.assertTrue(self.detail().json()["data"]["can_reopen"])
        self.assertEqual(
            self.post("/api/v1/tickets/public/track/reopen/", {"reason": "Issue persists."}).status_code,
            200,
        )

    def test_closed_ticket_without_closure_timestamp_cannot_reopen(self):
        self.ticket.status = TicketStatus.CLOSED
        self.ticket.save(update_fields=["status"])
        self.assertFalse(self.detail().json()["data"]["can_reopen"])
        self.assertEqual(
            self.post("/api/v1/tickets/public/track/reopen/", {"reason": "Still broken."}).status_code,
            400,
        )

    @override_settings(
        EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
        PUBLIC_APP_URL="https://support.example.com",
    )
    def test_closure_email_links_to_two_day_reopen_window(self):
        from apps.tickets.services.ack_service import send_ticket_closed_notice

        self.assertTrue(send_ticket_closed_notice(ticket=self.ticket, remarks="Verified fixed"))
        body = mail.outbox[-1].body
        self.assertIn("within 2 days (48 hours) of its closure", body)
        self.assertIn("https://support.example.com/track", body)
        self.assertIn(self.ticket.ticket_no, body)
        self.assertNotIn("reply to this message", body)

    def test_reopen_access_request_needs_approval_again(self):
        self.ticket.ticket_type = TicketType.ACCESS_REQUEST
        self.ticket.save(update_fields=["ticket_type"])
        self.close_ticket()
        self.verify()
        response = self.post("/api/v1/tickets/public/track/reopen/", {"reason": "Access broke again."})
        self.assertEqual(response.status_code, 200, response.content)
        self.ticket.refresh_from_db()
        self.assertEqual(self.ticket.status, TicketStatus.PENDING_APPROVAL)

    def test_bug_reopen_preserves_owner_and_adds_one_public_event(self):
        from apps.bugs.constants import BugStatus
        from apps.bugs.models import BugReopenHistory
        from apps.bugs.tests.factories import make_bug, make_masters

        project, module, priority, severity = make_masters()
        bug = make_bug(
            bug_no="BUG-2609-0022", reporter=self.owner, project=project,
            module=module, priority=priority, severity=severity,
            owner=self.owner, status=BugStatus.CLOSED,
        )
        bug.closed_at = timezone.now() - timedelta(hours=49)
        bug.save(update_fields=["closed_at"])
        self.ticket.bug = bug
        self.ticket.ticket_type = TicketType.BUG
        self.ticket.status = TicketStatus.CLOSED
        self.ticket.save(update_fields=["bug", "ticket_type", "status"])
        self.assertFalse(self.detail().json()["data"]["can_reopen"])
        self.assertEqual(
            self.post("/api/v1/tickets/public/track/reopen/", {"reason": "Still broken."}).status_code,
            400,
        )
        bug.closed_at = timezone.now() - timedelta(hours=1)
        bug.save(update_fields=["closed_at"])
        self.assertTrue(self.detail().json()["data"]["can_reopen"])
        response = self.post("/api/v1/tickets/public/track/reopen/", {"reason": "Still broken."})
        self.assertEqual(response.status_code, 200, response.content)
        bug.refresh_from_db()
        self.assertEqual(bug.status, BugStatus.REOPENED)
        self.assertEqual(bug.owner_id, self.owner.pk)
        self.assertEqual(BugReopenHistory.objects.filter(bug=bug).count(), 1)
        self.assertEqual(
            TicketActivity.objects.filter(ticket=self.ticket, event_type="BUG_STATUS_REOPENED").count(),
            1,
        )
