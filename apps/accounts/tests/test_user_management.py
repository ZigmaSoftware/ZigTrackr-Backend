"""User administration tests (spec 16).

This module exists because sameer/imran were created outside the app (a shell
session) with a password that did not match what was later typed at login,
and no role -- both silent failure modes a real create/reset/deactivate API
must not have.
"""

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import Client, TestCase

from apps.accounts.models import Role, RolePermission, Permission, UserRole
from apps.bugs.tests.factories import make_user
from common.permissions.codenames import PERMISSION_CATALOG, ROLE_PERMISSIONS

User = get_user_model()
PASSWORD = "Zigma@12345"


def seed_rbac():
    perms = {}
    for order, (codename, name, module, screen, screen_name, act) in enumerate(
            PERMISSION_CATALOG, start=1):
        perms[codename] = Permission.objects.create(
            codename=codename, name=name, module=module, screen_code=screen,
            screen_name=screen_name, action=act, sort_order=order * 10,
        )
    roles = {}
    for code, codenames in ROLE_PERMISSIONS.items():
        role = Role.objects.create(code=code, name=code.title(), is_system=True)
        RolePermission.objects.bulk_create([
            RolePermission(role=role, permission=perms[c]) for c in codenames if c in perms
        ])
        roles[code] = role
    return roles


class UserManagementTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.roles = seed_rbac()
        cls.admin = make_user("um_admin")
        cls.admin.set_password(PASSWORD)
        cls.admin.save()
        UserRole.objects.create(user=cls.admin, role=cls.roles["ADMIN"])

        cls.dev = make_user("um_dev")
        cls.dev.set_password(PASSWORD)
        cls.dev.save()
        UserRole.objects.create(user=cls.dev, role=cls.roles["DEVELOPER"])

    def setUp(self):
        cache.clear()
        self.client = Client()

    def login(self, user):
        response = self.client.post(
            "/api/v1/auth/login/",
            data={"username": user.username, "password": PASSWORD},
            content_type="application/json",
        )
        assert response.status_code == 200, response.content
        return self.client


class CreateUserTests(UserManagementTestCase):
    def test_create_without_removed_fields_keeps_model_defaults(self):
        self.login(self.admin)
        response = self.client.post(
            "/api/v1/users/", content_type="application/json",
            data={"username": "simpleprofile", "email": "simple@example.test",
                  "full_name": "Simple Profile", "employee_code": "NEW001", "phone": "1234567890",
                  "password": "StrongPass@123", "roles": ["DEVELOPER"]},
        )
        self.assertEqual(response.status_code, 201, response.content)
        user = User.objects.get(username="simpleprofile")
        self.assertEqual(user.designation, "")
        self.assertIsNone(user.department_id)
        self.assertIsNone(user.team_id)
        self.assertIsNone(user.site_id)
        self.assertTrue(user.check_password("StrongPass@123"))
        self.assertEqual(user.employee_code, "NEW001")
        self.assertEqual(user.phone, "1234567890")

    def test_removed_fields_and_fk_aliases_are_rejected_on_create(self):
        self.login(self.admin)
        for field in ("designation", "department", "team", "site", "department_id", "team_id", "site_id"):
            with self.subTest(field=field):
                response = self.client.post(
                    "/api/v1/users/", content_type="application/json",
                    data={"username": f"retired_{field}", "password": "StrongPass@123", field: None},
                )
                self.assertEqual(response.status_code, 400, response.content)
                self.assertIn(field, response.json()["errors"])
                self.assertFalse(User.objects.filter(username=f"retired_{field}").exists())

    def test_write_serializer_does_not_expose_removed_fields(self):
        from apps.accounts.serializers import UserWriteSerializer

        fields = UserWriteSerializer().fields
        self.assertTrue({"designation", "department", "team", "site"}.isdisjoint(fields))
        self.assertTrue({"username", "email", "password", "full_name", "employee_code", "phone", "roles"}.issubset(fields))

    def test_non_object_create_payload_is_validation_error(self):
        self.login(self.admin)
        for payload in ([], [{"team": None}], "not an object"):
            with self.subTest(payload=payload):
                response = self.client.post("/api/v1/users/", data=payload, content_type="application/json")
                self.assertEqual(response.status_code, 400, response.content)

    def test_admin_can_create_user_with_role(self):
        self.login(self.admin)
        response = self.client.post(
            "/api/v1/users/",
            data={"username": "newperson", "email": "newperson@example.com",
                  "password": "StrongPass@123", "full_name": "New Person",
                  "roles": ["DEVELOPER"]},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(response.json()["data"]["roles"][0]["code"], "DEVELOPER")

    def test_created_user_can_log_in_with_the_exact_password_set(self):
        """The core regression: the account created must authenticate with
        the password given, on the first attempt, every time."""
        self.login(self.admin)
        self.client.post(
            "/api/v1/users/",
            data={"username": "loginproof", "password": "ProofPass@123"},
            content_type="application/json",
        )
        fresh_client = Client()
        response = fresh_client.post(
            "/api/v1/auth/login/",
            data={"username": "loginproof", "password": "ProofPass@123"},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200, response.content)

    def test_created_user_has_working_permissions_from_assigned_role(self):
        self.login(self.admin)
        self.client.post(
            "/api/v1/users/",
            data={"username": "permproof", "password": "ProofPass@123",
                  "roles": ["DEVELOPER"]},
            content_type="application/json",
        )
        fresh_client = Client()
        response = fresh_client.post(
            "/api/v1/auth/login/",
            data={"username": "permproof", "password": "ProofPass@123"},
            content_type="application/json",
        )
        self.assertGreater(len(response.json()["data"]["permissions"]), 0)

    def test_user_created_with_no_roles_is_allowed_but_has_no_permissions(self):
        """Allowed (an admin may deliberately stage an account before
        assigning a role), but must not be silently mistaken for "has some
        default access"."""
        self.login(self.admin)
        response = self.client.post(
            "/api/v1/users/",
            data={"username": "noroleyet", "password": "ProofPass@123"},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["data"]["roles"], [])

    def test_duplicate_username_rejected(self):
        self.login(self.admin)
        response = self.client.post(
            "/api/v1/users/",
            data={"username": "um_dev", "password": "ProofPass@123"},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("username", response.json()["errors"])

    def test_weak_password_rejected(self):
        self.login(self.admin)
        response = self.client.post(
            "/api/v1/users/",
            data={"username": "weakpw", "password": "1234"},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)

    def test_developer_cannot_create_users(self):
        self.login(self.dev)
        response = self.client.post(
            "/api/v1/users/",
            data={"username": "shouldfail", "password": "ProofPass@123"},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 403)

    def test_create_is_audited(self):
        from apps.audit.models import AuditLog, AuditAction

        self.login(self.admin)
        self.client.post(
            "/api/v1/users/",
            data={"username": "auditcheck", "password": "ProofPass@123"},
            content_type="application/json",
        )
        self.assertTrue(
            AuditLog.objects.filter(action=AuditAction.USER_CREATED,
                                    new_value="auditcheck").exists()
        )


class UpdateUserTests(UserManagementTestCase):
    def test_edit_preserves_existing_organization_values(self):
        from apps.masters.models import DepartmentMaster, SiteMaster, TeamMaster

        self.dev.designation = "Existing developer"
        self.dev.department = DepartmentMaster.objects.create(name="Existing department")
        self.dev.team = TeamMaster.objects.create(name="Existing team")
        self.dev.site = SiteMaster.objects.create(name="Existing site")
        self.dev.save()
        previous = (self.dev.designation, self.dev.department_id, self.dev.team_id, self.dev.site_id)
        self.login(self.admin)
        response = self.client.patch(
            f"/api/v1/users/{self.dev.unique_id}/", content_type="application/json",
            data={"full_name": "Renamed Developer", "roles": ["DEVELOPER"]},
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.dev.refresh_from_db()
        self.assertEqual(self.dev.full_name, "Renamed Developer")
        self.assertEqual((self.dev.designation, self.dev.department_id, self.dev.team_id, self.dev.site_id), previous)

    def test_removed_inputs_rejected_without_partial_profile_changes(self):
        self.login(self.admin)
        previous_name = self.dev.full_name
        for field in ("designation", "department", "team", "site", "department_id", "team_id", "site_id"):
            with self.subTest(field=field):
                response = self.client.patch(
                    f"/api/v1/users/{self.dev.unique_id}/", content_type="application/json",
                    data={"full_name": "Must not be applied", "roles": [], field: None},
                )
                self.assertEqual(response.status_code, 400, response.content)
                self.assertIn(field, response.json()["errors"])
                self.dev.refresh_from_db()
                self.assertEqual(self.dev.full_name, previous_name)
                self.assertTrue(UserRole.objects.filter(user=self.dev, role=self.roles["DEVELOPER"], is_active=True).exists())

    def test_admin_can_update_profile_fields(self):
        self.login(self.admin)
        response = self.client.patch(
            f"/api/v1/users/{self.dev.unique_id}/",
            data={"full_name": "Updated Name"},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.dev.refresh_from_db()
        self.assertEqual(self.dev.full_name, "Updated Name")

    def test_update_does_not_touch_password(self):
        """A profile edit must never silently change the password -- that
        would be exactly the kind of surprise that produced this bug report."""
        original_hash = self.dev.password
        self.login(self.admin)
        self.client.patch(
            f"/api/v1/users/{self.dev.unique_id}/",
            data={"full_name": "Renamed", "password": "ShouldBeIgnored@123"},
            content_type="application/json",
        )
        self.dev.refresh_from_db()
        self.assertEqual(self.dev.password, original_hash)

    def test_admin_can_change_roles(self):
        self.login(self.admin)
        response = self.client.patch(
            f"/api/v1/users/{self.dev.unique_id}/",
            data={"roles": ["TESTER"]},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200, response.content)
        active_roles = {ur.role.code for ur in UserRole.objects.filter(
            user=self.dev, is_active=True)}
        self.assertEqual(active_roles, {"TESTER"})

    def test_removing_all_roles_leaves_user_without_permissions(self):
        self.login(self.admin)
        self.client.patch(
            f"/api/v1/users/{self.dev.unique_id}/",
            data={"roles": []}, content_type="application/json",
        )
        from common.permissions.require import resolve_permission_codes
        self.dev.refresh_from_db()
        self.assertEqual(resolve_permission_codes(self.dev), set())


class UserProfileServiceTests(UserManagementTestCase):
    def test_direct_service_calls_cannot_write_removed_fields(self):
        from django.core.exceptions import ValidationError
        from apps.accounts.services.user_service import create_user, update_user

        original_name = self.dev.full_name
        for field in ("designation", "department", "team", "site", "department_id", "team_id", "site_id"):
            with self.subTest(field=field):
                with self.assertRaises(ValidationError):
                    create_user(actor=self.admin, username=f"service_{field}", password="StrongPass@123", **{field: None})
                self.assertFalse(User.objects.filter(username=f"service_{field}").exists())
                with self.assertRaises(ValidationError):
                    update_user(user=self.dev, actor=self.admin, roles=[], full_name="Must not change", **{field: None})
                self.assertEqual(self.dev.full_name, original_name)
                self.assertTrue(UserRole.objects.filter(user=self.dev, role=self.roles["DEVELOPER"], is_active=True).exists())


class DeactivateReactivateTests(UserManagementTestCase):
    def test_admin_can_deactivate_user(self):
        self.login(self.admin)
        response = self.client.delete(f"/api/v1/users/{self.dev.unique_id}/")
        self.assertEqual(response.status_code, 200, response.content)
        self.dev.refresh_from_db()
        self.assertTrue(self.dev.is_deleted)
        self.assertFalse(self.dev.is_active)

    def test_deactivated_user_cannot_log_in(self):
        self.login(self.admin)
        self.client.delete(f"/api/v1/users/{self.dev.unique_id}/")
        fresh_client = Client()
        response = fresh_client.post(
            "/api/v1/auth/login/",
            data={"username": "um_dev", "password": PASSWORD},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 401)

    def test_deactivation_revokes_outstanding_sessions(self):
        """A deactivation must end a session already in progress, not merely
        block the next login attempt."""
        dev_client = Client()
        dev_client.post(
            "/api/v1/auth/login/",
            data={"username": "um_dev", "password": PASSWORD},
            content_type="application/json",
        )
        self.login(self.admin)
        self.client.delete(f"/api/v1/users/{self.dev.unique_id}/")

        csrf_response = dev_client.get("/api/v1/auth/csrf/")
        csrf_token = dev_client.cookies["csrftoken"].value
        refresh_response = dev_client.post(
            "/api/v1/auth/refresh/", HTTP_X_CSRFTOKEN=csrf_token,
        )
        self.assertEqual(refresh_response.status_code, 401)

    def test_admin_cannot_deactivate_self(self):
        self.login(self.admin)
        response = self.client.delete(f"/api/v1/users/{self.admin.unique_id}/")
        self.assertEqual(response.status_code, 400)
        self.admin.refresh_from_db()
        self.assertFalse(self.admin.is_deleted)

    def test_admin_can_reactivate_a_deactivated_user(self):
        self.login(self.admin)
        self.client.delete(f"/api/v1/users/{self.dev.unique_id}/")
        response = self.client.post(f"/api/v1/users/{self.dev.unique_id}/reactivate/")
        self.assertEqual(response.status_code, 200, response.content)
        self.dev.refresh_from_db()
        self.assertFalse(self.dev.is_deleted)
        self.assertTrue(self.dev.is_active)

    def test_reactivated_user_can_log_in_again(self):
        self.login(self.admin)
        self.client.delete(f"/api/v1/users/{self.dev.unique_id}/")
        self.client.post(f"/api/v1/users/{self.dev.unique_id}/reactivate/")
        fresh_client = Client()
        response = fresh_client.post(
            "/api/v1/auth/login/",
            data={"username": "um_dev", "password": PASSWORD},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)

    def test_developer_cannot_deactivate_anyone(self):
        self.login(self.dev)
        response = self.client.delete(f"/api/v1/users/{self.admin.unique_id}/")
        self.assertEqual(response.status_code, 403)


class PasswordResetTests(UserManagementTestCase):
    def test_admin_can_reset_password(self):
        self.login(self.admin)
        response = self.client.post(
            f"/api/v1/users/{self.dev.unique_id}/reset-password/",
            data={"new_password": "BrandNew@456"},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200, response.content)

    def test_new_password_works_and_old_one_does_not(self):
        self.login(self.admin)
        self.client.post(
            f"/api/v1/users/{self.dev.unique_id}/reset-password/",
            data={"new_password": "BrandNew@456"},
            content_type="application/json",
        )
        fresh_client = Client()
        old = fresh_client.post(
            "/api/v1/auth/login/",
            data={"username": "um_dev", "password": PASSWORD},
            content_type="application/json",
        )
        self.assertEqual(old.status_code, 401)

        new = fresh_client.post(
            "/api/v1/auth/login/",
            data={"username": "um_dev", "password": "BrandNew@456"},
            content_type="application/json",
        )
        self.assertEqual(new.status_code, 200)

    def test_reset_revokes_outstanding_sessions(self):
        dev_client = Client()
        dev_client.post(
            "/api/v1/auth/login/",
            data={"username": "um_dev", "password": PASSWORD},
            content_type="application/json",
        )
        self.login(self.admin)
        self.client.post(
            f"/api/v1/users/{self.dev.unique_id}/reset-password/",
            data={"new_password": "BrandNew@456"},
            content_type="application/json",
        )
        csrf_token = dev_client.cookies["csrftoken"].value
        refresh_response = dev_client.post(
            "/api/v1/auth/refresh/", HTTP_X_CSRFTOKEN=csrf_token,
        )
        self.assertEqual(refresh_response.status_code, 401)

    def test_weak_reset_password_rejected(self):
        self.login(self.admin)
        response = self.client.post(
            f"/api/v1/users/{self.dev.unique_id}/reset-password/",
            data={"new_password": "123"},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)

    def test_developer_cannot_reset_anyone_elses_password(self):
        self.login(self.dev)
        response = self.client.post(
            f"/api/v1/users/{self.admin.unique_id}/reset-password/",
            data={"new_password": "Whatever@123"},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 403)

    def test_reset_is_audited(self):
        from apps.audit.models import AuditLog, AuditAction

        self.login(self.admin)
        self.client.post(
            f"/api/v1/users/{self.dev.unique_id}/reset-password/",
            data={"new_password": "BrandNew@456"},
            content_type="application/json",
        )
        self.assertTrue(
            AuditLog.objects.filter(
                action=AuditAction.USER_PASSWORD_RESET,
                entity_unique_id=self.dev.unique_id,
            ).exists()
        )
