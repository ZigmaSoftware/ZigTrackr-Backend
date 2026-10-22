"""Company boundaries and developer-owned routing metadata."""

from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import UserRole
from apps.accounts.services.user_service import create_user
from apps.audit.models import AuditLog
from apps.bugs.tests.factories import make_user
from apps.bugs.tests.test_api import seed_rbac
from apps.mail_intake.models import MailIntake
from apps.mail_intake.services.thread_service import find_thread_ticket, apply_thread_update
from apps.masters.models import CompanyMaster, ProjectMaster, PriorityMaster, SeverityMaster
from apps.notifications.models import Notification
from apps.notifications.services import notify
from apps.tickets.models import SupportTicket
from apps.tickets.services.routing_service import route_ticket
from apps.tickets.tests.factories import make_ticket


class CompanyWorkDetailsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        roles = seed_rbac()
        cls.a = CompanyMaster.objects.create(code="TENANT_A", name="Company A", support_email="a@example.com")
        cls.b = CompanyMaster.objects.create(code="TENANT_B", name="Company B", support_email="b@example.com")
        cls.super = make_user("tenant_super", is_superuser=True)
        cls.members = {}
        for company in (cls.a, cls.b):
            for role in ("ADMIN", "TEAM_LEAD", "DEVELOPER", "TESTER"):
                user = make_user(f"{company.code}_{role}", company=company)
                UserRole.objects.create(user=user, role=roles[role])
                cls.members[company.code, role] = user
        cls.admin = cls.members[cls.a.code, "ADMIN"]
        cls.dev = cls.members[cls.a.code, "DEVELOPER"]
        cls.foreign_dev = cls.members[cls.b.code, "DEVELOPER"]
        cls.projects = {company.pk: ProjectMaster.objects.create(company=company, code="ERP", name=f"{company.name} ERP") for company in (cls.a, cls.b)}
        cls.priority = PriorityMaster.objects.create(company=cls.a, code="HIGH", name="High", rank=20)
        cls.severity = SeverityMaster.objects.create(company=cls.a, code="MAJOR", name="Major", rank=20)
        cls.foreign_priority = PriorityMaster.objects.create(company=cls.b, code="HIGH", name="High", rank=20)

    def client_for(self, user):
        client = APIClient()
        client.force_authenticate(user)
        return client

    def routed(self):
        ticket = make_ticket(company=self.a, reporter=self.admin, ticket_type="BUG", needs_review=False)
        return route_ticket(ticket=ticket, actor=self.admin, ticket_type="BUG", owner=self.dev)

    def test_each_role_cannot_read_foreign_ticket_or_bug(self):
        foreign = make_ticket(company=self.b, reporter=self.foreign_dev, owner=self.foreign_dev,
                              ticket_type="SERVICE_REQUEST", needs_review=False, status="TESTING")
        for role in ("ADMIN", "TEAM_LEAD", "DEVELOPER", "TESTER"):
            with self.subTest(role=role):
                client = self.client_for(self.members[self.a.code, role])
                self.assertEqual(client.get(f"/api/v1/tickets/{foreign.unique_id}/").status_code, 404)
                response = client.get("/api/v1/tickets/")
                self.assertEqual(response.status_code, 200, response.data)
                self.assertNotIn(str(foreign.unique_id), [row["id"] for row in response.json()["data"]["results"]])

    def test_unassigned_full_mail_threads_remain_company_scoped(self):
        own = make_ticket(company=self.a, reporter=self.admin)
        foreign = make_ticket(company=self.b, reporter=self.foreign_dev)
        for ticket, marker in ((own, "Own full mail"), (foreign, "Other company private mail")):
            MailIntake.objects.create(company=ticket.company, linked_ticket=ticket,
                dedupe_key=f"full-intake-{ticket.pk}", from_email="sender@example.com",
                body_text=f"Dear Team,\n{marker}\nRegards,\nSender", received_at=timezone.now())
        client = self.client_for(self.admin)
        response = client.get("/api/v1/tickets/", {"submodule": "unassigned", "unassigned": "true"})
        self.assertEqual(response.status_code, 200, response.data)
        rows = response.json()["data"]["results"]
        self.assertEqual([row["id"] for row in rows], [str(own.unique_id)])
        self.assertIn("Dear Team,", rows[0]["description_messages"][0]["body"])
        self.assertNotIn("Other company private mail", str(response.json()))
        self.assertNotIn("description_messages", client.get("/api/v1/tickets/").json()["data"]["results"][0])

    def test_super_admin_sees_all_companies(self):
        response = self.client_for(self.super).get("/api/v1/companies/")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertTrue({str(self.a.unique_id), str(self.b.unique_id)} <= {row["id"] for row in response.json()["data"]["results"]})

    def test_company_admin_directory_and_masters_are_scoped(self):
        client = self.client_for(self.admin)
        for endpoint in ("projects", "priorities", "users", "companies"):
            response = client.get(f"/api/v1/{endpoint}/")
            self.assertEqual(response.status_code, 200, response.data)
            if endpoint == "companies":
                self.assertEqual([row["id"] for row in response.json()["data"]["results"]], [str(self.a.unique_id)])
            else:
                self.assertTrue(all(row["company_id"] == str(self.a.unique_id) for row in response.json()["data"]["results"]))

    def test_admin_cannot_change_companies_or_global_roles(self):
        client = self.client_for(self.admin)
        self.assertEqual(client.post("/api/v1/companies/", {"code": "NO", "name": "No"}).status_code, 403)
        response = client.patch(f"/api/v1/projects/{self.projects[self.a.pk].unique_id}/",
            {"company": str(self.b.unique_id)}, format="json")
        self.assertEqual(response.status_code, 400, response.data)
        role = self.admin.user_roles.first().role
        response = client.put(f"/api/v1/roles/{role.unique_id}/permissions/", {"permissions": []}, format="json")
        self.assertEqual(response.status_code, 403, response.data)

    def test_admin_cannot_create_admin_or_foreign_company_user(self):
        for company, roles in ((self.a, ["ADMIN"]), (self.b, ["DEVELOPER"])):
            with self.assertRaises(Exception):
                create_user(actor=self.admin, company=company, username="cannot_create", password="sample", roles=roles)

    def test_foreign_owner_rejected_even_for_super_admin(self):
        ticket = make_ticket(company=self.a, reporter=self.admin)
        response = self.client_for(self.super).post(f"/api/v1/tickets/{ticket.unique_id}/review/",
            {"ticket_type": "BUG", "owner": str(self.foreign_dev.unique_id)}, format="json")
        self.assertEqual(response.status_code, 400, response.data)
        ticket.refresh_from_db()
        self.assertIsNone(ticket.owner_id)
        self.assertIsNone(ticket.bug_id)

    def test_employee_codes_are_unique_within_company_not_globally(self):
        client = self.client_for(self.super)
        for index, company in enumerate((self.a, self.b, self.a)):
            response = client.post("/api/v1/users/", {"username": f"employee_scope_{index}",
                "company": str(company.unique_id), "employee_code": "SHARED001", "password": "Scope-test!90482",
                "roles": ["DEVELOPER"]}, format="json")
            self.assertEqual(response.status_code, 201 if index < 2 else 400, response.data)

    def test_company_filter_never_widens_access(self):
        foreign = make_ticket(company=self.b, reporter=self.foreign_dev)
        response = self.client_for(self.admin).get("/api/v1/tickets/", {"company": str(self.b.unique_id)})
        self.assertEqual(response.json()["data"]["results"], [])
        response = self.client_for(self.super).get("/api/v1/tickets/", {"company": str(self.b.unique_id)})
        self.assertEqual([row["id"] for row in response.json()["data"]["results"]], [str(foreign.unique_id)])

    @patch("apps.tickets.services.outbound_mail.queue_assigned_notice")
    def test_tl_assignment_requires_only_type_and_developer(self, _queue):
        ticket = make_ticket(company=self.a, reporter=self.admin)
        response = self.client_for(self.members[self.a.code, "TEAM_LEAD"]).post(
            f"/api/v1/tickets/{ticket.unique_id}/review/",
            {"ticket_type": "BUG", "owner": str(self.dev.unique_id)}, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        ticket.refresh_from_db()
        self.assertEqual(ticket.owner_id, self.dev.pk)
        self.assertIsNone(ticket.project_id)
        self.assertIsNone(ticket.bug.priority_id)
        self.assertEqual(ticket.bug.company_id, self.a.pk)

    def details(self):
        return {"company": str(self.a.unique_id), "project": str(self.projects[self.a.pk].unique_id),
                "priority": str(self.priority.unique_id), "severity": str(self.severity.unique_id), "environment": "UAT"}

    def test_start_requires_work_details_and_owner(self):
        ticket = self.routed()
        url = f"/api/v1/tickets/{ticket.unique_id}/work-transition/"
        self.assertEqual(self.client_for(self.dev).post(url, {"to_status": "IN_PROGRESS"}, format="json").status_code, 400)
        self.assertEqual(self.client_for(self.admin).post(url,
            {"to_status": "IN_PROGRESS", "details": self.details()}, format="json").status_code, 403)
        response = self.client_for(self.dev).post(url,
            {"to_status": "IN_PROGRESS", "details": self.details()}, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        ticket.refresh_from_db()
        self.assertEqual(ticket.bug.status, "IN_PROGRESS")
        self.assertEqual(ticket.priority_id, ticket.bug.priority_id)
        self.assertEqual(ticket.environment, "UAT")

    def test_developer_can_edit_before_rectification_only(self):
        ticket = self.routed()
        client = self.client_for(self.dev)
        url = f"/api/v1/tickets/{ticket.unique_id}/work-details/"
        response = client.patch(url, self.details(), format="json")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertTrue(response.json()["data"]["can_edit_details"])
        ticket.bug.status = "TESTING"
        ticket.bug.save(update_fields=["status"])
        self.assertEqual(client.patch(url, {"title": "Forbidden edit"}, format="json").status_code, 403)
        self.assertFalse(client.get(f"/api/v1/tickets/{ticket.unique_id}/").json()["data"]["can_edit_details"])
        response = client.patch(f"/api/v1/bugs/{ticket.bug.unique_id}/", {"title": "Alternate endpoint edit"}, format="json")
        self.assertIn(response.status_code, (403, 404))

    def test_foreign_master_uuid_cannot_be_submitted(self):
        ticket = self.routed()
        response = self.client_for(self.dev).patch(f"/api/v1/tickets/{ticket.unique_id}/work-details/",
            {"priority": str(self.foreign_priority.unique_id)}, format="json")
        self.assertEqual(response.status_code, 400, response.data)

    def test_failed_start_rolls_back_work_details(self):
        ticket = self.routed()
        response = self.client_for(self.dev).post(f"/api/v1/tickets/{ticket.unique_id}/work-transition/",
            {"to_status": "IN_PROGRESS", "details": {"title": "Must roll back"}}, format="json")
        self.assertEqual(response.status_code, 400, response.data)
        ticket.refresh_from_db()
        self.assertNotEqual(ticket.title, "Must roll back")

    def test_foreign_email_thread_never_links(self):
        ticket = self.routed()
        mail = MailIntake.objects.create(company=self.b, dedupe_key="foreign-thread",
            subject=f"Re: {ticket.ticket_no}", received_at=timezone.now())
        self.assertFalse(find_thread_ticket(mail).matched)
        with self.assertRaises(Exception):
            apply_thread_update(mail=mail, ticket=ticket)

    def test_identical_email_message_ids_are_independent_per_company(self):
        from apps.mail_intake.tests.factories import make_parsed
        from apps.mail_intake.services.duplicate_service import register_mail
        parsed = make_parsed(message_id="<shared-across-mailboxes@example.com>")
        first, created = register_mail(parsed, dedupe_key="same-key", mailbox_address=self.a.support_email)
        self.assertTrue(created)
        second, created = register_mail(parsed, dedupe_key="same-key", mailbox_address=self.b.support_email)
        self.assertTrue(created)
        repeat, created = register_mail(parsed, dedupe_key="same-key", mailbox_address=self.a.support_email)
        self.assertFalse(created)
        self.assertEqual(first.pk, repeat.pk)
        self.assertNotEqual(first.pk, second.pk)
        self.assertEqual(first.company_id, self.a.pk)
        self.assertEqual(second.company_id, self.b.pk)

    def test_notifications_and_audit_do_not_leak(self):
        ticket = self.routed()
        rows = notify(recipients=[self.admin, self.foreign_dev, self.super], actor=self.dev,
            ticket=ticket, notification_type="TICKET_ASSIGNED", title="Company A confidential")
        self.assertEqual({row.recipient_id for row in rows}, {self.admin.pk, self.super.pk})
        self.assertTrue(AuditLog.objects.filter(entity_unique_id=ticket.unique_id, company=self.a).exists())
        Notification.objects.create(recipient=self.foreign_dev, ticket=ticket, title="Old leak", notification_type="TICKET_ASSIGNED")
        response = self.client_for(self.foreign_dev).get("/api/v1/notifications/")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertFalse(response.json()["data"]["results"])
