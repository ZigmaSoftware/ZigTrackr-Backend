"""Organisation masters: department, team, site."""

from django.conf import settings
from django.db import models

from common.models import NamedMaster


class DepartmentMaster(NamedMaster):
    code = models.CharField(max_length=20, blank=True, default="")

    class Meta:
        db_table = "department_master"
        ordering = ["name"]
        indexes = [
            models.Index(fields=["is_active", "is_deleted"], name="idx_department_active"),
        ]


class TeamMaster(NamedMaster):
    code = models.CharField(max_length=20, blank=True, default="")
    team_lead = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="led_teams", db_column="team_lead_id",
    )

    class Meta:
        db_table = "team_master"
        ordering = ["name"]
        indexes = [
            models.Index(fields=["is_active", "is_deleted"], name="idx_team_active"),
        ]


class SiteMaster(NamedMaster):
    code = models.CharField(max_length=20, blank=True, default="")
    address = models.TextField(blank=True, default="")

    class Meta:
        db_table = "site_master"
        ordering = ["name"]
        indexes = [
            models.Index(fields=["is_active", "is_deleted"], name="idx_site_active"),
        ]
