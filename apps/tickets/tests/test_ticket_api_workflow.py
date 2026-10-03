"""Role and state checks for assigned service and access requests."""

from datetime import timedelta

from django.test import Client, TestCase
from django.utils import timezone

from apps.accounts.models import UserRole
from apps.audit.models import AuditAction, AuditLog
from apps.bugs.tests.factories import make_team, make_user
from apps.bugs.tests.test_api import seed_rbac
from apps.bugs.tests.test_api import PASSWORD
from apps.mail_intake.models import MailIntake
from apps.tickets.constants import TicketStatus, TicketType
from apps.tickets.models import OutboundMail
from apps.tickets.tests.factories import make_ticket


class UnassignedTicketQueueTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        roles = seed_rbac()
        cls.admin = make_user("queue_admin")
        cls.lead = make_user("queue_lead")
        for user, role in ((cls.admin, "ADMIN"), (cls.lead, "TEAM_LEAD")):
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

    def test_received_date_uses_mail_date_and_manual_creation_date(self):
        mail_ticket = make_ticket()
        received_at = timezone.now() - timedelta(days=2)
        MailIntake.objects.create(
            dedupe_key="queue-date-filter-original", from_email="sender@example.com",
            received_at=received_at, linked_ticket=mail_ticket,
        )
        manual_ticket = make_ticket(source="MANUAL")
        client = self.login(self.admin)

        old_date = timezone.localtime(received_at).date().isoformat()
        old_rows = client.get(
            "/api/v1/tickets/", {"unassigned": "true", "received_date": old_date}
        ).json()["data"]["results"]
        self.assertEqual([row["id"] for row in old_rows], [str(mail_ticket.unique_id)])

        today_rows = client.get(
            "/api/v1/tickets/", {"unassigned": "true", "received_date": timezone.localdate().isoformat()}
        ).json()["data"]["results"]
        self.assertIn(str(manual_ticket.unique_id), [row["id"] for row in today_rows])
        self.assertNotIn(str(mail_ticket.unique_id), [row["id"] for row in today_rows])

    def test_admin_soft_deletes_unassigned_ticket_with_audit(self):
        ticket = make_ticket()
        mail = MailIntake.objects.create(
            dedupe_key="queue-delete-mail", from_email="sender@example.com",
            received_at=timezone.now(), linked_ticket=ticket,
        )
        client = self.login(self.admin)
        queue_rows = client.get("/api/v1/tickets/", {"unassigned": "true"}).json()["data"]["results"]
        self.assertTrue(next(row for row in queue_rows if row["id"] == str(ticket.unique_id))["can_delete"])
        response = client.delete(f"/api/v1/tickets/{ticket.unique_id}/")
        self.assertEqual(response.status_code, 200, response.content)
        ticket.refresh_from_db()
        mail.refresh_from_db()
        self.assertTrue(ticket.is_deleted)
        self.assertEqual(mail.linked_ticket_id, ticket.pk)
        self.assertTrue(AuditLog.objects.filter(
            entity_unique_id=ticket.unique_id, action=AuditAction.TICKET_DELETED,
        ).exists())
        self.assertEqual(client.get(f"/api/v1/tickets/{ticket.unique_id}/").status_code, 404)

    def test_non_admin_cannot_delete_and_assigned_ticket_is_protected(self):
        unassigned = make_ticket()
        lead_client = self.login(self.lead)
        lead_rows = lead_client.get("/api/v1/tickets/", {"unassigned": "true"}).json()["data"]["results"]
        self.assertFalse(next(row for row in lead_rows if row["id"] == str(unassigned.unique_id))["can_delete"])
        lead_response = lead_client.delete(
            f"/api/v1/tickets/{unassigned.unique_id}/"
        )
        self.assertEqual(lead_response.status_code, 403, lead_response.content)
        unassigned.refresh_from_db()
        self.assertFalse(unassigned.is_deleted)

        assigned = make_ticket(owner=self.admin)
        admin_response = self.login(self.admin).delete(
            f"/api/v1/tickets/{assigned.unique_id}/"
        )
        self.assertEqual(admin_response.status_code, 400, admin_response.content)
        assigned.refresh_from_db()
        self.assertFalse(assigned.is_deleted)


class AssignedRequestApiTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        roles = seed_rbac()
        team = make_team("Request team")
        cls.lead = make_user("request_lead", team=team)
        cls.developer = make_user("request_developer", team=team)
        for user in (cls.lead, cls.developer):
            user.set_password(PASSWORD)
            user.save()
        UserRole.objects.create(user=cls.lead, role=roles["TEAM_LEAD"])
        UserRole.objects.create(user=cls.developer, role=roles["DEVELOPER"])

    def setUp(self):
        self.client = Client()

    def post_as(self, user, ticket, action, payload=None):
        self.client = Client()
        login = self.client.post(
            "/api/v1/auth/login/",
            data={"username": user.username, "password": PASSWORD},
            content_type="application/json",
        )
        self.assertEqual(login.status_code, 200, login.content)
        return self.client.post(
            f"/api/v1/tickets/{ticket.unique_id}/{action}/",
            data=payload or {},
            content_type="application/json",
        )

    def test_assigned_developer_can_start_and_complete_service(self):
        ticket = make_ticket(
            ticket_type=TicketType.SERVICE_REQUEST, needs_review=False,
            status=TicketStatus.ASSIGNED, owner=self.developer,
        )
        self.assertEqual(self.post_as(self.developer, ticket, "start").status_code, 200)
        ticket.refresh_from_db()
        self.assertEqual(ticket.status, TicketStatus.IN_PROGRESS)
        self.assertEqual(self.post_as(self.developer, ticket, "complete").status_code, 200)
        ticket.refresh_from_db()
        self.assertEqual(ticket.status, TicketStatus.COMPLETED)

    def test_direct_close_queues_requester_notice(self):
        ticket = make_ticket(
            ticket_type=TicketType.SERVICE_REQUEST, needs_review=False,
            status=TicketStatus.COMPLETED, owner=self.developer,
            reported_by_email="requester@example.com",
        )
        response = self.post_as(self.developer, ticket, "close", {"remarks": "Done"})
        self.assertEqual(response.status_code, 200, response.content)
        self.assertTrue(OutboundMail.objects.filter(
            ticket=ticket, kind="CLOSED", status=OutboundMail.Status.PENDING,
        ).exists())

    def test_access_requires_approval_before_developer_implements(self):
        ticket = make_ticket(
            ticket_type=TicketType.ACCESS_REQUEST, needs_review=False,
            status=TicketStatus.PENDING_APPROVAL, owner=self.developer,
        )
        self.assertEqual(self.post_as(self.developer, ticket, "implement").status_code, 400)
        self.assertEqual(self.post_as(self.lead, ticket, "approve").status_code, 200)
        self.assertEqual(self.post_as(self.developer, ticket, "implement").status_code, 200)
        ticket.refresh_from_db()
        self.assertEqual(ticket.status, TicketStatus.COMPLETED)
        self.assertEqual(
            list(ticket.activities.values_list("event_type", flat=True)),
            ["ACCESS_APPROVED", "ACCESS_IMPLEMENTED"],
        )

    def test_review_ticket_cannot_be_assigned_or_started(self):
        ticket = make_ticket(
            ticket_type=TicketType.SERVICE_REQUEST, needs_review=True,
            status=TicketStatus.NEEDS_REVIEW,
        )
        self.assertEqual(
            self.post_as(self.lead, ticket, "assign", {"owner": str(self.developer.unique_id)}).status_code,
            400,
        )
        ticket.owner = self.developer  # A ticket assigned before the guard was added.
        ticket.save(update_fields=["owner"])
        self.assertEqual(self.post_as(self.developer, ticket, "start").status_code, 400)


class TicketAttachmentApiTests(TestCase):
    """Upload rules. The reason is required, unlike on bug attachments."""

    @classmethod
    def setUpTestData(cls):
        roles = seed_rbac()
        team = make_team("Attachment team")
        cls.developer = make_user("attach_developer", team=team)
        cls.lead = make_user("attach_lead", team=team)
        for user in (cls.developer, cls.lead):
            user.set_password(PASSWORD)
            user.save()
        UserRole.objects.create(user=cls.developer, role=roles["DEVELOPER"])
        UserRole.objects.create(user=cls.lead, role=roles["TEAM_LEAD"])

    def setUp(self):
        from django.core.cache import cache

        cache.clear()

    def login(self, user):
        client = Client()
        login = client.post(
            "/api/v1/auth/login/",
            data={"username": user.username, "password": PASSWORD},
            content_type="application/json",
        )
        self.assertEqual(login.status_code, 200, login.content)
        return client

    def make_upload(self, name="evidence.txt", body=b"screenshot bytes"):
        from django.core.files.uploadedfile import SimpleUploadedFile

        return SimpleUploadedFile(name, body, content_type="text/plain")

    def ticket(self):
        return make_ticket(
            ticket_type=TicketType.SERVICE_REQUEST, needs_review=False,
            status=TicketStatus.ASSIGNED, owner=self.developer,
        )

    def test_upload_requires_a_reason(self):
        ticket = self.ticket()
        response = self.login(self.developer).post(
            f"/api/v1/tickets/{ticket.unique_id}/attachments/",
            data={"file": self.make_upload()},
        )
        self.assertEqual(response.status_code, 400, response.content)
        self.assertIn("reason", response.json()["errors"])

    def test_upload_stores_the_reason_and_lists_it(self):
        ticket = self.ticket()
        client = self.login(self.developer)
        upload = client.post(
            f"/api/v1/tickets/{ticket.unique_id}/attachments/",
            data={"file": self.make_upload(), "reason": "Error screen from the user"},
        )
        self.assertEqual(upload.status_code, 201, upload.content)

        listing = client.get(f"/api/v1/tickets/{ticket.unique_id}/attachments/")
        self.assertEqual(listing.status_code, 200)
        rows = listing.json()["data"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["reason"], "Error screen from the user")
        self.assertEqual(rows[0]["file_name"], "evidence.txt")
        # Never a direct MEDIA_ROOT path (spec 31).
        self.assertTrue(rows[0]["download_url"].startswith("/api/v1/tickets/attachments/"))

    def test_upload_without_a_file_is_rejected(self):
        ticket = self.ticket()
        response = self.login(self.developer).post(
            f"/api/v1/tickets/{ticket.unique_id}/attachments/",
            data={"reason": "No file attached"},
        )
        self.assertEqual(response.status_code, 400, response.content)
        self.assertIn("file", response.json()["errors"])

    def test_developer_may_upload_but_not_delete(self):
        """Deletion is a lead/admin capability -- a developer must not be able to
        remove evidence from a ticket they happen to own."""
        ticket = self.ticket()
        client = self.login(self.developer)
        upload = client.post(
            f"/api/v1/tickets/{ticket.unique_id}/attachments/",
            data={"file": self.make_upload(), "reason": "To be removed"},
        )
        attachment_id = upload.json()["data"]["id"]

        deleted = client.delete(f"/api/v1/tickets/attachments/{attachment_id}/")
        self.assertEqual(deleted.status_code, 403, deleted.content)

        listing = client.get(f"/api/v1/tickets/{ticket.unique_id}/attachments/")
        self.assertEqual(len(listing.json()["data"]), 1)

    def test_lead_delete_removes_it_from_the_listing(self):
        ticket = self.ticket()
        uploader = self.login(self.developer)
        upload = uploader.post(
            f"/api/v1/tickets/{ticket.unique_id}/attachments/",
            data={"file": self.make_upload(), "reason": "To be removed"},
        )
        attachment_id = upload.json()["data"]["id"]

        lead_client = self.login(self.lead)
        deleted = lead_client.delete(f"/api/v1/tickets/attachments/{attachment_id}/")
        self.assertEqual(deleted.status_code, 200, deleted.content)

        listing = lead_client.get(f"/api/v1/tickets/{ticket.unique_id}/attachments/")
        self.assertEqual(listing.json()["data"], [])

    def test_mail_attachment_appears_in_same_ticket_tab(self):
        from django.utils import timezone
        from apps.mail_intake.models import MailAttachment, MailIntake

        ticket = self.ticket()
        mail = MailIntake.objects.create(
            dedupe_key="mail-attachment-list-test", from_email="sender@example.com",
            received_at=timezone.now(), linked_ticket=ticket,
        )
        attachment = MailAttachment.objects.create(
            mail=mail, file_name="error.png", file_type="image/png",
            file_extension="png", file_size=128, file_path="private/error.png",
        )
        response = self.login(self.developer).get(
            f"/api/v1/tickets/{ticket.unique_id}/attachments/",
        )
        self.assertEqual(response.status_code, 200, response.content)
        rows = response.json()["data"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["source"], "MAIL")
        self.assertIn(str(attachment.unique_id), rows[0]["download_url"])

    def test_mail_source_filter_excludes_manual_uploads_and_includes_replies(self):
        from django.utils import timezone
        from apps.mail_intake.models import MailAttachment, MailIntake

        ticket = self.ticket()
        original = MailIntake.objects.create(
            dedupe_key="mail-attachment-source-original", from_email="sender@example.com",
            received_at=timezone.now(), linked_ticket=ticket,
        )
        MailAttachment.objects.create(
            mail=original, file_name="original.png", file_type="image/png",
            file_extension="png", file_size=128, file_path="private/original.png",
        )
        reply = MailIntake.objects.create(
            dedupe_key="mail-attachment-source-reply", from_email="sender@example.com",
            received_at=timezone.now(), linked_ticket=ticket, is_thread_reply=True,
        )
        MailAttachment.objects.create(
            mail=reply, file_name="reply.pdf", file_type="application/pdf",
            file_extension="pdf", file_size=256, file_path="private/reply.pdf",
        )

        upload = self.login(self.developer).post(
            f"/api/v1/tickets/{ticket.unique_id}/attachments/",
            data={"file": self.make_upload(), "reason": "Internal evidence"},
        )
        self.assertEqual(upload.status_code, 201, upload.content)

        response = self.login(self.developer).get(
            f"/api/v1/tickets/{ticket.unique_id}/attachments/?source=MAIL",
        )
        self.assertEqual(response.status_code, 200, response.content)
        rows = response.json()["data"]
        self.assertEqual([row["file_name"] for row in rows], ["original.png", "reply.pdf"])
        self.assertEqual([row["reason"] for row in rows], ["Original email", "Email reply"])
        self.assertTrue(all(row["source"] == "MAIL" for row in rows))

    def test_attachment_source_filter_rejects_unknown_values(self):
        ticket = self.ticket()
        response = self.login(self.developer).get(
            f"/api/v1/tickets/{ticket.unique_id}/attachments/?source=OTHER",
        )
        self.assertEqual(response.status_code, 400, response.content)


class TicketTimelineApiTests(TestCase):
    """The timeline folds in the linked bug's history, which is where a BUG
    ticket's real work is recorded."""

    @classmethod
    def setUpTestData(cls):
        roles = seed_rbac()
        team = make_team("Timeline team")
        cls.lead = make_user("timeline_lead", team=team)
        cls.developer = make_user("timeline_developer", team=team)
        for user in (cls.lead, cls.developer):
            user.set_password(PASSWORD)
            user.save()
        UserRole.objects.create(user=cls.lead, role=roles["TEAM_LEAD"])
        UserRole.objects.create(user=cls.developer, role=roles["DEVELOPER"])

    def login(self, user):
        client = Client()
        login = client.post(
            "/api/v1/auth/login/",
            data={"username": user.username, "password": PASSWORD},
            content_type="application/json",
        )
        self.assertEqual(login.status_code, 200, login.content)
        return client

    def test_service_ticket_timeline_lists_its_updates(self):
        from apps.tickets.services.ticket_service import add_ticket_update

        ticket = make_ticket(
            ticket_type=TicketType.SERVICE_REQUEST, needs_review=False,
            status=TicketStatus.ASSIGNED, owner=self.developer,
        )
        add_ticket_update(ticket=ticket, update_text="Checked the mailbox.",
                          actor=self.developer)

        response = self.login(self.developer).get(
            f"/api/v1/tickets/{ticket.unique_id}/timeline/")
        self.assertEqual(response.status_code, 200, response.content)
        rows = response.json()["data"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["description"], "Checked the mailbox.")
        self.assertEqual(rows[0]["actor"], self.developer.display_name)

    def test_bug_ticket_timeline_includes_the_bugs_history(self):
        """Without this, a confirmed BUG ticket showed an empty timeline even
        though the bug had been created, assigned and progressed."""
        from apps.tickets.services.ticket_service import review_ticket
        from apps.tickets.tests.factories import make_masters

        project, module, priority, severity = make_masters()
        ticket = review_ticket(
            ticket=make_ticket(ticket_type=TicketType.UNKNOWN, needs_review=True),
            actor=self.lead,
            ticket_type=TicketType.BUG,
            project=project,
            priority=priority,
            severity=severity,
            owner=self.developer,
        )
        self.assertIsNotNone(ticket.bug_id, "review should have created the bug")

        response = self.login(self.developer).get(
            f"/api/v1/tickets/{ticket.unique_id}/timeline/")
        self.assertEqual(response.status_code, 200, response.content)
        rows = response.json()["data"]
        self.assertTrue(rows, "bug history should appear on the ticket timeline")
        self.assertTrue(any(row["type"] == "STATUS" for row in rows))

    def test_timeline_is_oldest_first(self):
        from apps.tickets.services.ticket_service import add_ticket_update

        ticket = make_ticket(
            ticket_type=TicketType.SERVICE_REQUEST, needs_review=False,
            status=TicketStatus.ASSIGNED, owner=self.developer,
        )
        add_ticket_update(ticket=ticket, update_text="First.", actor=self.developer)
        add_ticket_update(ticket=ticket, update_text="Second.", actor=self.developer)

        rows = self.login(self.developer).get(
            f"/api/v1/tickets/{ticket.unique_id}/timeline/").json()["data"]
        self.assertEqual([row["description"] for row in rows], ["First.", "Second."])
