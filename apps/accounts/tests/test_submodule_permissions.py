"""Full submodule bundles without silently expanding other role grants."""

from django.core.cache import cache
from django.test import TestCase
from rest_framework.test import APIClient

from apps.accounts.models import Permission, RolePermission, UserRole
from apps.accounts.services.permission_service import PROTECTED_ADMIN_PERMISSIONS
from apps.accounts.tests.test_user_management import seed_rbac
from apps.audit.models import AuditAction, AuditLog
from apps.bugs.tests.factories import make_user
from common.permissions.require import resolve_permission_codes
from common.permissions.ticket_submodules import TICKET_SUBMODULES


class SubmodulePermissionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.roles = seed_rbac()
        cls.admin = make_user("bundle_admin")
        cls.developer = make_user("bundle_developer")
        cls.manager = make_user("bundle_manager")
        for user, code in ((cls.admin, "ADMIN"), (cls.developer, "DEVELOPER"), (cls.manager, "MANAGEMENT")):
            UserRole.objects.create(user=user, role=cls.roles[code])

    def setUp(self):
        cache.clear()
        self.client = APIClient()
        self.client.force_authenticate(self.admin)

    def granted(self, code):
        return set(RolePermission.objects.filter(role=self.roles[code]).values_list("permission__codename", flat=True))

    def change(self, role, changes):
        return self.client.put(f"/api/v1/roles/{self.roles[role].unique_id}/permissions/",
                               {"submodule_changes": changes}, format="json")

    def test_matrix_contains_one_submodule_per_catalog_screen(self):
        response = self.client.get("/api/v1/permissions/matrix/")
        self.assertEqual(response.status_code, 200, response.content)
        modules = response.json()["data"]["modules"]
        groups = [submodule for module in modules for submodule in module["submodules"]]
        self.assertEqual(len(groups), len({group["key"] for group in groups}))
        user = next(group for group in groups if group["key"] == "administration.user")
        self.assertEqual(user["name"], "User Management")
        self.assertEqual(set(user["permissions"]), {"admin.user.view", "admin.user.add", "admin.user.edit", "admin.user.delete"})
        protected = next(group for group in groups if group["key"] == "administration.permission")
        self.assertEqual(protected["protected_roles"], ["ADMIN"])

    def test_ticket_creation_and_management_show_actual_sidebar_submodules(self):
        modules = {m["module"]: m for m in self.client.get("/api/v1/permissions/matrix/").json()["data"]["modules"]}
        self.assertEqual([s["name"] for s in modules["ticket_creation"]["submodules"]],
                         ["Create Ticket", "Unassigned Tickets", "Reassign Tickets"])
        self.assertEqual([s["name"] for s in modules["ticket_management"]["submodules"]],
                         [name for _, name, group, _, _ in TICKET_SUBMODULES if group == "ticket_management"])
        self.assertNotIn("tickets", modules)
        cells = [p["codename"] for module in modules.values() for p in module["permissions"]]
        self.assertEqual(len(cells), len(set(cells)))

    def test_enable_ticket_submodule_grants_actions_but_not_global_ownership(self):
        RolePermission.objects.filter(role=self.roles["DEVELOPER"], permission__codename="tickets.unassigned.access").delete()
        before = self.granted("DEVELOPER")
        response = self.change("DEVELOPER", [{"key": "ticket_creation.unassigned", "enabled": True}])
        self.assertEqual(response.status_code, 200, response.content)
        after = self.granted("DEVELOPER")
        self.assertTrue({"tickets.unassigned.access", "tickets.ticket.classify", "tickets.ticket.assign", "tickets.ticket.delete"}.issubset(after))
        self.assertNotIn("tickets.ticket.mutate_all", after)
        self.assertNotIn("tickets.ticket.view_all", after)
        self.assertEqual(before - after, set())

    def test_disabling_one_ticket_screen_keeps_other_screens_and_shared_actions(self):
        self.change("DEVELOPER", [{"key": "ticket_management.bugs", "enabled": True}])
        before = self.granted("DEVELOPER")
        response = self.change("DEVELOPER", [{"key": "ticket_management.services", "enabled": False}])
        self.assertEqual(response.status_code, 200, response.content)
        after = self.granted("DEVELOPER")
        self.assertNotIn("tickets.services.access", after)
        self.assertIn("tickets.bugs.access", after)
        self.assertIn("tickets.ticket.add_update", after)
        self.assertIn("tickets.ticket.verify_close", after)
        self.assertEqual(before - after, {"tickets.services.access"})

    def test_batch_enable_disable_shared_actions_is_order_independent(self):
        for changes in (
            [{"key": "ticket_management.services", "enabled": False}, {"key": "ticket_management.bugs", "enabled": True}],
            [{"key": "ticket_management.bugs", "enabled": True}, {"key": "ticket_management.services", "enabled": False}],
        ):
            response = self.change("DEVELOPER", changes)
            self.assertEqual(response.status_code, 200, response.content)
            self.assertIn("tickets.ticket.verify_close", self.granted("DEVELOPER"))
            self.assertNotIn("tickets.services.access", self.granted("DEVELOPER"))

    def test_disabling_all_ticket_menus_keeps_daily_updates_read_dependency(self):
        changes = [{"key": f"{group}.{key}", "enabled": False} for key, _, group, _, _ in TICKET_SUBMODULES]
        response = self.change("DEVELOPER", changes)
        self.assertEqual(response.status_code, 200, response.content)
        after = self.granted("DEVELOPER")
        self.assertIn("bugs.update.view", after)
        self.assertIn("tickets.ticket.view", after)
        self.assertFalse(any(f"tickets.{key}.access" in after for key, *_ in TICKET_SUBMODULES))

    def test_page_gate_is_checked_by_ticket_list_api(self):
        from apps.tickets.tests.factories import make_ticket
        from apps.tickets.constants import TicketType

        service = make_ticket(reporter=self.developer, ticket_type=TicketType.SERVICE_REQUEST)
        make_ticket(reporter=self.developer, ticket_type=TicketType.BUG)
        self.change("DEVELOPER", [{"key": "ticket_management.bugs", "enabled": False}])
        self.client.force_authenticate(type(self.developer).objects.get(pk=self.developer.pk))
        self.assertEqual(self.client.get("/api/v1/tickets/", {"submodule": "bugs"}).status_code, 403)
        response = self.client.get("/api/v1/tickets/", {"submodule": "services"})
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual([row["id"] for row in response.json()["data"]["results"]], [str(service.unique_id)])
        # An extra query filter can narrow, but cannot replace the page preset.
        response = self.client.get("/api/v1/tickets/", {"submodule": "services", "ticket_type": "BUG"})
        self.assertEqual(response.json()["data"]["results"], [])
        self.assertEqual(self.client.get("/api/v1/tickets/", {"submodule": "fake"}).status_code, 400)

    def test_existing_role_upgrade_adds_only_page_grants_and_is_idempotent(self):
        import importlib
        from types import SimpleNamespace
        from django.apps import apps
        from django.db import connection

        migration = importlib.import_module("apps.accounts.migrations.0003_ticket_submodule_access")
        Permission.objects.filter(codename__in=[f"tickets.{key}.access" for key, *_ in TICKET_SUBMODULES]).delete()
        before = self.granted("MANAGEMENT")
        migration.forwards(apps, SimpleNamespace(connection=connection))
        after = self.granted("MANAGEMENT")
        expected = {f"tickets.{key}.access" for key, _, _, legacy, _ in TICKET_SUBMODULES if legacy in before}
        self.assertEqual(after - before, expected)
        self.assertNotIn("tickets.ticket.assign", after)
        self.assertNotIn("access.request.approve", after)
        migration.forwards(apps, SimpleNamespace(connection=connection))
        self.assertEqual(self.granted("MANAGEMENT"), after)

    def test_enable_grants_all_actions_and_preserves_other_partial_grants(self):
        before = self.granted("MANAGEMENT")
        response = self.change("MANAGEMENT", [{"key": "administration.user", "enabled": True}])
        self.assertEqual(response.status_code, 200, response.content)
        bundle = {"admin.user.view", "admin.user.add", "admin.user.edit", "admin.user.delete"}
        self.assertEqual(self.granted("MANAGEMENT"), before | bundle)
        self.assertNotIn("bugs.bug.mutate_all", self.granted("MANAGEMENT"))
        user = type(self.manager).objects.get(pk=self.manager.pk)
        self.assertTrue(bundle.issubset(resolve_permission_codes(user)))

    def test_disable_removes_all_submodule_actions_and_nothing_else(self):
        before = self.granted("DEVELOPER")
        group = set(Permission.objects.filter(module="bugs", screen_code="update").values_list("codename", flat=True))
        response = self.change("DEVELOPER", [{"key": "bugs.update", "enabled": False}])
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(self.granted("DEVELOPER"), before - group)

    def test_untouched_roles_are_not_changed(self):
        before = self.granted("DEVELOPER")
        self.change("MANAGEMENT", [{"key": "administration.user", "enabled": True}])
        self.assertEqual(self.granted("DEVELOPER"), before)

    def test_inactive_or_deleted_actions_are_not_granted(self):
        Permission.objects.filter(codename="admin.user.edit").update(is_active=False)
        Permission.objects.filter(codename="admin.user.delete").update(is_deleted=True)
        response = self.change("DEVELOPER", [{"key": "administration.user", "enabled": True}])
        self.assertEqual(response.status_code, 200, response.content)
        codes = self.granted("DEVELOPER")
        self.assertIn("admin.user.add", codes)
        self.assertNotIn("admin.user.edit", codes)
        self.assertNotIn("admin.user.delete", codes)

    def test_invalid_submodule_or_duplicate_changes_are_atomic(self):
        before = self.granted("DEVELOPER")
        for changes in ([{"key": "administration.user", "enabled": True}, {"key": "fake.module", "enabled": True}],
                        [{"key": "administration.user", "enabled": True}, {"key": "administration.user", "enabled": False}],
                        [{"key": "administration.user"}], [], "invalid"):
            with self.subTest(changes=changes):
                response = self.change("DEVELOPER", changes)
                self.assertEqual(response.status_code, 400, response.content)
                self.assertEqual(self.granted("DEVELOPER"), before)

    def test_admin_cannot_disable_protected_submodules(self):
        before = self.granted("ADMIN")
        for key in ("administration.role", "administration.permission"):
            with self.subTest(key=key):
                response = self.change("ADMIN", [{"key": key, "enabled": False}, {"key": "administration.user", "enabled": False}])
                self.assertEqual(response.status_code, 400, response.content)
                self.assertEqual(self.granted("ADMIN"), before)

    def test_read_only_matrix_permission_does_not_allow_writes(self):
        role = self.roles["MANAGEMENT"]
        RolePermission.objects.create(role=role, permission=Permission.objects.get(codename="admin.permission.view"))
        fresh_manager = type(self.manager).objects.get(pk=self.manager.pk)
        self.client.force_authenticate(fresh_manager)
        self.assertEqual(self.client.get("/api/v1/permissions/matrix/").status_code, 200)
        self.assertEqual(self.change("DEVELOPER", [{"key": "administration.user", "enabled": True}]).status_code, 403)

    def test_anonymous_or_unprivileged_users_cannot_read_or_write_matrix(self):
        for user in (None, self.developer):
            with self.subTest(user=user):
                self.client.force_authenticate(user)
                self.assertIn(self.client.get("/api/v1/permissions/matrix/").status_code, (401, 403))
                self.assertIn(self.change("DEVELOPER", [{"key": "administration.user", "enabled": True}]).status_code, (401, 403))

    def test_legacy_action_payload_keeps_admin_guard_and_replace_semantics(self):
        path = f"/api/v1/roles/{self.roles['ADMIN'].unique_id}/permissions/"
        response = self.client.put(path, {"permissions": []}, format="json")
        self.assertEqual(response.status_code, 400)
        response = self.client.put(path, {"permissions": sorted(PROTECTED_ADMIN_PERMISSIONS)}, format="json")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(self.granted("ADMIN"), set(PROTECTED_ADMIN_PERMISSIONS))

    def test_cannot_mix_legacy_and_submodule_payloads(self):
        path = f"/api/v1/roles/{self.roles['DEVELOPER'].unique_id}/permissions/"
        before = self.granted("DEVELOPER")
        for data in ({}, {"permissions": [], "submodule_changes": [{"key": "administration.user", "enabled": True}]}):
            response = self.client.put(path, data, format="json")
            self.assertEqual(response.status_code, 400)
            self.assertEqual(self.granted("DEVELOPER"), before)

    def test_repeated_delivery_is_idempotent_and_change_is_audited(self):
        changes = [{"key": "administration.user", "enabled": True}]
        self.change("DEVELOPER", changes)
        before = self.granted("DEVELOPER")
        self.change("DEVELOPER", changes)
        self.assertEqual(self.granted("DEVELOPER"), before)
        event = AuditLog.objects.filter(action=AuditAction.PERMISSION_CHANGED).first()
        self.assertEqual(event.metadata["submodule_changes"], changes)
