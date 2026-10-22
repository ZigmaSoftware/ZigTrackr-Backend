"""Company administrators can manage their company, never their own authority."""

from django.db import IntegrityError, transaction
from django.test import TestCase
from unittest.mock import patch
from rest_framework.test import APIClient

from apps.accounts.models import CompanyRole, Role, User, UserRole
from apps.audit.models import AuditLog
from apps.bugs.tests.factories import make_user
from apps.bugs.tests.test_api import seed_rbac
from apps.masters.models import CompanyMaster
from common.permissions.require import has_permission, resolve_permission_codes, user_ids_with_permission


class CompanyAdministrationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.roles = seed_rbac()
        cls.a = CompanyMaster.objects.create(code="ADMIN_A", name="Company A")
        cls.b = CompanyMaster.objects.create(code="ADMIN_B", name="Company B")
        cls.super = make_user("only_super_admin", is_superuser=True, company=None)
        cls.admin = make_user("company_admin_a", company=cls.a)
        cls.other_admin = make_user("company_admin_b", company=cls.b)
        cls.developer = make_user("company_developer_a", company=cls.a)
        cls.other_developer = make_user("company_developer_b", company=cls.b)
        for user, role in ((cls.admin, "ADMIN"), (cls.other_admin, "ADMIN"),
                           (cls.developer, "DEVELOPER"), (cls.other_developer, "DEVELOPER"),
                           (cls.super, "ADMIN"), (cls.super, "DEVELOPER")):
            UserRole.objects.create(user=user, role=cls.roles[role])

    def client_for(self, user):
        client = APIClient()
        client.force_authenticate(User.objects.get(pk=user.pk))
        return client

    def permission_url(self, role="DEVELOPER", company=None):
        path = f"/api/v1/roles/{self.roles[role].unique_id}/permissions/"
        return f"{path}?company={company.unique_id}" if company else path

    def test_admin_has_full_administration_by_default(self):
        codes = resolve_permission_codes(self.admin)
        self.assertTrue({"admin.user.view", "admin.user.add", "admin.user.edit", "admin.user.delete",
            "admin.role.view", "admin.role.manage", "admin.permission.view", "admin.permission.manage", "admin.audit.view"} <= codes)
        for path in ("users", "roles", "permissions/matrix", "audit"):
            response = self.client_for(self.admin).get(f"/api/v1/{path}/")
            self.assertEqual(response.status_code, 200, response.data)

    def test_admin_role_edit_changes_only_own_company(self):
        role = self.roles["DEVELOPER"]
        original = role.name
        response = self.client_for(self.admin).patch(f"/api/v1/roles/{role.unique_id}/",
            {"name": "Company A Engineer"}, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.json()["data"]["name"], "Company A Engineer")
        role.refresh_from_db()
        self.assertEqual(role.name, original)
        response = self.client_for(self.other_admin).get(f"/api/v1/roles/{role.unique_id}/")
        self.assertEqual(response.json()["data"]["name"], original)
        self.assertTrue(AuditLog.objects.filter(entity_type="CompanyRole", company=self.a).exists())

    def test_admin_permission_edits_apply_only_to_own_company(self):
        response = self.client_for(self.admin).put(self.permission_url(), {"permissions": ["bugs.bug.view"]}, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(resolve_permission_codes(User.objects.get(pk=self.developer.pk)), {"bugs.bug.view"})
        self.assertIn("tickets.ticket.view", resolve_permission_codes(self.other_developer))
        recipients = user_ids_with_permission("tickets.ticket.view")
        self.assertNotIn(self.developer.pk, recipients)
        self.assertIn(self.other_developer.pk, recipients)
        self.assertEqual(response.json()["data"]["company"]["id"], str(self.a.unique_id))

    def test_admin_cannot_select_foreign_company(self):
        client = self.client_for(self.admin)
        for path in (f"/api/v1/roles/?company={self.b.unique_id}",
                     f"/api/v1/permissions/matrix/?company={self.b.unique_id}"):
            self.assertEqual(client.get(path).status_code, 403)
        self.assertEqual(client.put(self.permission_url(company=self.b), {"permissions": []}, format="json").status_code, 403)
        self.assertFalse(CompanyRole.objects.exists())

    def test_only_super_admin_can_change_admin_permissions_or_metadata(self):
        client = self.client_for(self.admin)
        self.assertEqual(client.put(self.permission_url("ADMIN"), {"permissions": []}, format="json").status_code, 403)
        self.assertEqual(client.patch(f"/api/v1/roles/{self.roles['ADMIN'].unique_id}/",
            {"name": "Unauthorized"}, format="json").status_code, 403)
        response = self.client_for(self.super).put(self.permission_url("ADMIN", self.a),
            {"submodule_changes": [{"key": "administration.permission", "enabled": False}]}, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertFalse(has_permission(User.objects.get(pk=self.admin.pk), "admin.permission.manage"))
        self.assertTrue(has_permission(self.other_admin, "admin.permission.manage"))
        self.assertEqual(self.client_for(self.admin).put(self.permission_url(), {"permissions": []}, format="json").status_code, 403)

    def test_super_admin_can_remove_global_admin_permissions_without_losing_access(self):
        response = self.client_for(self.super).put(self.permission_url("ADMIN"), {"permissions": []}, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertTrue(has_permission(self.super, "admin.permission.manage"))
        self.assertEqual(self.client_for(self.super).get("/api/v1/permissions/matrix/").status_code, 200)
        self.assertEqual(self.client_for(self.admin).get("/api/v1/users/").status_code, 403)

    def test_super_admin_cells_editable_admin_cells_locked(self):
        for user, locked in ((self.super, False), (self.admin, True)):
            response = self.client_for(user).get("/api/v1/permissions/matrix/")
            groups = [group for module in response.json()["data"]["modules"] for group in module["submodules"]]
            self.assertTrue(all(("ADMIN" in group["protected_roles"]) == locked for group in groups))
            admin_role = next(role for role in response.json()["data"]["roles"] if role["code"] == "ADMIN")
            self.assertEqual(admin_role["can_manage"], not locked)

    def test_super_admin_not_listed_or_accessible_in_user_management(self):
        for user in (self.super, self.admin):
            client = self.client_for(user)
            response = client.get("/api/v1/users/", {"include_inactive": "true"})
            self.assertNotIn(str(self.super.unique_id), [row["id"] for row in response.json()["data"]["results"]])
            self.assertEqual(client.get(f"/api/v1/users/{self.super.unique_id}/").status_code, 404)
            self.assertEqual(client.post(f"/api/v1/users/{self.super.unique_id}/reset-password/",
                {"new_password": "Cannot-reset-Super-123!"}, format="json").status_code, 404)
            response = client.get("/api/v1/users/assignable/")
            self.assertNotIn(str(self.super.unique_id), [row["id"] for row in response.json()["data"]])

    def test_database_blocks_second_super_admin_and_flag_bypasses(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            make_user("second_super", is_superuser=True)
        with self.assertRaises(IntegrityError), transaction.atomic():
            User.objects.filter(pk=self.developer.pk).update(is_superuser=True)
        with self.assertRaises(IntegrityError), transaction.atomic():
            User.objects.filter(pk=self.developer.pk).update(is_superuser=True, super_admin_slot=1)
        self.assertEqual(User.objects.filter(is_superuser=True).count(), 1)

    def test_local_role_deactivation_does_not_disable_other_company(self):
        role = self.roles["DEVELOPER"]
        response = self.client_for(self.admin).patch(f"/api/v1/roles/{role.unique_id}/", {"is_active": False}, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(resolve_permission_codes(User.objects.get(pk=self.developer.pk)), set())
        self.assertTrue(resolve_permission_codes(self.other_developer))

    def test_access_refresh_is_published_only_after_successful_commit(self):
        with patch("apps.accounts.services.permission_service.publish_role_access_changed") as publish:
            with self.captureOnCommitCallbacks(execute=True):
                response = self.client_for(self.admin).put(self.permission_url(),
                    {"permissions": ["bugs.bug.view"]}, format="json")
                self.assertEqual(response.status_code, 200, response.data)
                publish.assert_not_called()
            publish.assert_called_once_with(self.roles["DEVELOPER"].pk, self.a.pk)
