"""Project / Module / Submodule hierarchy."""

from django.conf import settings
from django.db import models

from common.models import NamedMaster


class ProjectMaster(NamedMaster):
    code = models.CharField(max_length=20)
    project_lead = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="led_projects", db_column="project_lead_id",
    )
    start_date = models.DateField(null=True, blank=True)

    class Meta:
        db_table = "project_master"
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(fields=["code"], name="uq_project_master_code"),
        ]
        indexes = [
            models.Index(fields=["is_active", "is_deleted"], name="idx_project_active"),
        ]


class ModuleMaster(NamedMaster):
    project = models.ForeignKey(
        ProjectMaster, on_delete=models.PROTECT,
        related_name="modules", db_column="project_id",
    )
    code = models.CharField(max_length=20, blank=True, default="")

    class Meta:
        db_table = "module_master"
        ordering = ["project__name", "name"]
        constraints = [
            # Module names are unique within a project, not globally: two
            # projects may each legitimately have a "Sales" module.
            models.UniqueConstraint(fields=["project", "name"], name="uq_module_project_name"),
        ]
        indexes = [
            models.Index(fields=["project", "is_active"], name="idx_module_project_active"),
        ]


class SubmoduleMaster(NamedMaster):
    module = models.ForeignKey(
        ModuleMaster, on_delete=models.PROTECT,
        related_name="submodules", db_column="module_id",
    )
    code = models.CharField(max_length=20, blank=True, default="")

    class Meta:
        db_table = "submodule_master"
        ordering = ["module__name", "name"]
        constraints = [
            models.UniqueConstraint(fields=["module", "name"], name="uq_submodule_module_name"),
        ]
        indexes = [
            models.Index(fields=["module", "is_active"], name="idx_submodule_module_active"),
        ]
