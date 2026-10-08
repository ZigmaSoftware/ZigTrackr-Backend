"""Master hierarchy writes use the UUIDs returned by dropdown list APIs."""

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from apps.masters.models import ModuleMaster, ProjectMaster, SubmoduleMaster


class MasterTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = get_user_model().objects.create(username="masters-admin", is_superuser=True)
        cls.project = ProjectMaster.objects.create(code="FIRST", name="First project")
        cls.other_project = ProjectMaster.objects.create(code="SECOND", name="Second project")
        cls.module = ModuleMaster.objects.create(project=cls.project, name="Accounts")

    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(self.admin)


class MasterHierarchyTests(MasterTestCase):
    def test_module_accepts_project_id_from_project_list(self):
        projects = self.client.get("/api/v1/projects/").json()["data"]["results"]
        project_id = next(row["id"] for row in projects if row["name"] == self.project.name)
        response = self.client.post("/api/v1/modules/", {"project": project_id, "name": "Purchase"}, format="json")
        self.assertEqual(response.status_code, 201, response.content)
        row = response.json()["data"]
        self.assertEqual(row["project_id"], project_id)
        self.assertNotIn("project", row)
        self.assertEqual(ModuleMaster.objects.get(unique_id=row["id"]).project, self.project)

    def test_submodule_accepts_module_id_from_module_list(self):
        modules = self.client.get("/api/v1/modules/").json()["data"]["results"]
        module_id = modules[0]["id"]
        response = self.client.post("/api/v1/submodules/", {"module": module_id, "name": "Invoices"}, format="json")
        self.assertEqual(response.status_code, 201, response.content)
        row = response.json()["data"]
        self.assertEqual(row["module_id"], module_id)
        self.assertNotIn("module", row)
        self.assertEqual(SubmoduleMaster.objects.get(unique_id=row["id"]).module, self.module)

    def test_edit_keeps_the_parent_uuid_returned_by_the_api(self):
        response = self.client.get(f"/api/v1/modules/{self.module.unique_id}/")
        row = response.json()["data"]
        response = self.client.patch(
            f"/api/v1/modules/{row['id']}/",
            {"project": row["project_id"], "description": "Updated description"}, format="json",
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.module.refresh_from_db()
        self.assertEqual(self.module.project, self.project)
        self.assertEqual(self.module.description, "Updated description")

    def test_module_names_are_unique_within_the_selected_project(self):
        response = self.client.post(
            "/api/v1/modules/", {"project": str(self.project.unique_id), "name": self.module.name}, format="json",
        )
        self.assertEqual(response.status_code, 400, response.content)
        response = self.client.post(
            "/api/v1/modules/", {"project": str(self.other_project.unique_id), "name": self.module.name}, format="json",
        )
        self.assertEqual(response.status_code, 201, response.content)

    def test_deleted_parents_cannot_be_selected(self):
        self.project.soft_delete()
        self.module.soft_delete()
        for resource, field, parent in [
            ("modules", "project", self.project), ("submodules", "module", self.module),
        ]:
            with self.subTest(resource=resource):
                response = self.client.post(
                    f"/api/v1/{resource}/", {field: str(parent.unique_id), "name": "New child"}, format="json",
                )
                self.assertEqual(response.status_code, 400, response.content)

    def test_master_writes_still_require_permission(self):
        reader = get_user_model().objects.create(username="masters-reader")
        self.client.force_authenticate(reader)
        response = self.client.post(
            "/api/v1/modules/", {"project": str(self.project.unique_id), "name": "Restricted"}, format="json",
        )
        self.assertEqual(response.status_code, 403, response.content)
        self.assertFalse(ModuleMaster.objects.filter(name="Restricted").exists())

    def test_invalid_parent_uuid_returns_field_validation(self):
        for resource, field in [("modules", "project"), ("submodules", "module")]:
            with self.subTest(resource=resource):
                response = self.client.post(
                    f"/api/v1/{resource}/", {field: "invalid-uuid", "name": "Invalid parent"}, format="json",
                )
                self.assertEqual(response.status_code, 400, response.content)
                self.assertIn(field, response.json()["errors"])

    def test_partial_rename_is_scoped_to_the_existing_parent(self):
        ModuleMaster.objects.create(project=self.other_project, name="Purchase")
        response = self.client.patch(
            f"/api/v1/modules/{self.module.unique_id}/", {"name": "Purchase"}, format="json",
        )
        self.assertEqual(response.status_code, 200, response.content)

    def test_reparenting_cannot_create_a_duplicate_module_name(self):
        ModuleMaster.objects.create(project=self.other_project, name=self.module.name)
        response = self.client.patch(
            f"/api/v1/modules/{self.module.unique_id}/", {"project": str(self.other_project.unique_id)}, format="json",
        )
        self.assertEqual(response.status_code, 400, response.content)


class MasterCrudTests(MasterTestCase):
    """Exercise the shared CRUD path and classification defaults for all masters."""

    def test_project_options_hide_inactive_projects_but_edit_lists_can_include_them(self):
        inactive = ProjectMaster.objects.create(code="INACTIVE", name="Inactive project", is_active=False)
        active_ids = {row["id"] for row in self.client.get("/api/v1/projects/").json()["data"]["results"]}
        self.assertNotIn(str(inactive.unique_id), active_ids)
        all_ids = {
            row["id"] for row in self.client.get("/api/v1/projects/", {"include_inactive": "true"}).json()["data"]["results"]
        }
        self.assertIn(str(inactive.unique_id), all_ids)

    def test_all_master_resources_can_create_edit_and_deactivate(self):
        from apps.masters.models import (
            DepartmentMaster, PriorityMaster, RootCauseTypeMaster,
            SeverityMaster, SiteMaster, TeamMaster,
        )

        resources = [
            ("projects", ProjectMaster, {"code": "CHECK_PROJECT"}),
            ("modules", ModuleMaster, {"project": str(self.project.unique_id)}),
            ("submodules", SubmoduleMaster, {"module": str(self.module.unique_id)}),
            ("priorities", PriorityMaster, {"code": "CHECK_PRIORITY", "sla_days": None}),
            ("severities", SeverityMaster, {"code": "CHECK_SEVERITY"}),
            ("root-cause-types", RootCauseTypeMaster, {"code": "CHECK_CAUSE"}),
            ("departments", DepartmentMaster, {}),
            ("teams", TeamMaster, {}),
            ("sites", SiteMaster, {"address": "Verification address"}),
        ]
        for resource, model, extra in resources:
            with self.subTest(resource=resource):
                response = self.client.post(
                    f"/api/v1/{resource}/", {"name": f"Check {resource}", **extra}, format="json",
                )
                self.assertEqual(response.status_code, 201, response.content)
                row_id = response.json()["data"]["id"]
                instance = model.objects.get(unique_id=row_id)
                if hasattr(instance, "rank"):
                    self.assertEqual(instance.rank, 100)
                response = self.client.patch(
                    f"/api/v1/{resource}/{row_id}/", {"description": "Edited"}, format="json",
                )
                self.assertEqual(response.status_code, 200, response.content)
                response = self.client.delete(f"/api/v1/{resource}/{row_id}/")
                self.assertEqual(response.status_code, 204, response.content)
                instance.refresh_from_db()
                self.assertTrue(instance.is_deleted)

    def test_seeded_classification_codes_and_deletion_remain_protected(self):
        from apps.masters.models import PriorityMaster, RootCauseTypeMaster, SeverityMaster

        for resource, model in [
            ("priorities", PriorityMaster), ("severities", SeverityMaster),
            ("root-cause-types", RootCauseTypeMaster),
        ]:
            with self.subTest(resource=resource):
                instance = model.objects.create(code="SYSTEM", name="System", is_system=True)
                response = self.client.patch(
                    f"/api/v1/{resource}/{instance.unique_id}/", {"code": "CHANGED"}, format="json",
                )
                self.assertEqual(response.status_code, 400, response.content)
                response = self.client.delete(f"/api/v1/{resource}/{instance.unique_id}/")
                self.assertEqual(response.status_code, 409, response.content)
                instance.refresh_from_db()
                self.assertFalse(instance.is_deleted)
