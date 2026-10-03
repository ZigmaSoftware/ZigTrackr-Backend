"""Classification masters: priority, severity, root cause type.

These are CodedMaster subclasses: user-editable through the Masters screens
(spec 16) but carrying a stable `code` that dashboard and report code filters
on, so renaming "Critical" to "P1" does not silently break the KPI cards.
"""

from django.db import models

from common.models import CodedMaster


class PriorityMaster(CodedMaster):
    # Default offset applied to reported_date to suggest an expected closure
    # date, so spec 12's overdue logic has data to work with from day one.
    sla_days = models.PositiveSmallIntegerField(null=True, blank=True)

    class Meta:
        db_table = "priority_master"
        ordering = ["rank", "name"]
        constraints = [
            models.UniqueConstraint(fields=["code"], name="uq_priority_master_code"),
        ]
        indexes = [
            models.Index(fields=["is_active", "rank"], name="idx_priority_active_rank"),
        ]


class SeverityMaster(CodedMaster):
    class Meta:
        db_table = "severity_master"
        ordering = ["rank", "name"]
        constraints = [
            models.UniqueConstraint(fields=["code"], name="uq_severity_master_code"),
        ]
        indexes = [
            models.Index(fields=["is_active", "rank"], name="idx_severity_active_rank"),
        ]


class RootCauseTypeMaster(CodedMaster):
    class Meta:
        db_table = "root_cause_type_master"
        ordering = ["rank", "name"]
        constraints = [
            models.UniqueConstraint(fields=["code"], name="uq_root_cause_type_code"),
        ]
        indexes = [
            models.Index(fields=["is_active"], name="idx_rct_active"),
        ]
