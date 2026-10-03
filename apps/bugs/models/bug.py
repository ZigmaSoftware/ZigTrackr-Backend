"""The Bug model (spec 39.1).

Holds current state for fast list and report loading. All history lives in the
append-only tables alongside it -- spec 65: current state for operations,
immutable history for traceability.
"""

from django.conf import settings
from django.db import models

from apps.bugs.constants import BugStatus, Environment, VerificationResult
from common.models import BaseMaster


class Bug(BaseMaster):
    id = models.BigAutoField(primary_key=True)

    # ---- IDENTITY ----
    bug_no = models.CharField(max_length=20, editable=False)

    # ---- CLASSIFICATION ----
    project = models.ForeignKey(
        "masters.ProjectMaster", on_delete=models.PROTECT,
        null=True, blank=True,
        related_name="bugs", db_column="project_id",
    )
    module = models.ForeignKey(
        "masters.ModuleMaster", on_delete=models.PROTECT, null=True, blank=True,
        related_name="bugs", db_column="module_id",
    )
    submodule = models.ForeignKey(
        "masters.SubmoduleMaster", on_delete=models.PROTECT, null=True, blank=True,
        related_name="bugs", db_column="submodule_id",
    )

    # ---- CONTENT ----
    title = models.CharField(max_length=255)
    description = models.TextField()

    # ---- REPORTING ----
    reported_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
        related_name="reported_bugs", db_column="reported_by",
    )
    reported_date = models.DateField(null=True, blank=True)
    department = models.ForeignKey(
        "masters.DepartmentMaster", on_delete=models.PROTECT, null=True, blank=True,
        related_name="bugs", db_column="department_id",
    )
    site = models.ForeignKey(
        "masters.SiteMaster", on_delete=models.PROTECT, null=True, blank=True,
        related_name="bugs", db_column="site_id",
    )
    environment = models.CharField(
        max_length=20, choices=Environment.choices, default=Environment.PRODUCTION,
        null=True, blank=True,
    )

    # ---- TRIAGE ----
    priority = models.ForeignKey(
        "masters.PriorityMaster", on_delete=models.PROTECT,
        null=True, blank=True,
        related_name="bugs", db_column="priority_id",
    )
    severity = models.ForeignKey(
        "masters.SeverityMaster", on_delete=models.PROTECT,
        null=True, blank=True,
        related_name="bugs", db_column="severity_id",
    )
    status = models.CharField(max_length=20, choices=BugStatus.choices, default=BugStatus.NEW)

    # ---- OWNERSHIP (current state; history in bug_assignment_history) ----
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
        related_name="owned_bugs", db_column="owner_id",
    )
    assigned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
        related_name="assigned_bugs", db_column="assigned_by",
    )
    assigned_date = models.DateTimeField(null=True, blank=True)

    # ---- SCHEDULE ----
    expected_closure_date = models.DateField(null=True, blank=True)

    # ---- DENORMALISED LATEST (spec 10 sanctions this for fast loading) ----
    latest_remarks = models.TextField(blank=True, default="")
    latest_update_at = models.DateTimeField(null=True, blank=True)
    # Date-only copy, indexed. MariaDB cannot use an index for
    # DATE(latest_update_at) = CURDATE(), so the spec 11 "not updated today"
    # query needs a plain DateField to stay an index range scan.
    latest_update_date = models.DateField(null=True, blank=True)
    next_action = models.CharField(max_length=255, blank=True, default="")

    # ---- HOLD / REJECT REASONS (spec 30) ----
    hold_reason = models.TextField(blank=True, default="")
    pending_reason = models.TextField(blank=True, default="")
    rejection_reason = models.TextField(blank=True, default="")

    # ---- ROOT CAUSE & RESOLUTION ----
    root_cause_type = models.ForeignKey(
        "masters.RootCauseTypeMaster", on_delete=models.PROTECT, null=True, blank=True,
        related_name="bugs", db_column="root_cause_type_id",
    )
    root_cause = models.TextField(blank=True, default="")
    resolution = models.TextField(blank=True, default="")
    resolved_date = models.DateField(null=True, blank=True)
    resolved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
        related_name="resolved_bugs", db_column="resolved_by",
    )

    # ---- TESTING / VERIFICATION ----
    tested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
        related_name="tested_bugs", db_column="tested_by",
    )
    verification_result = models.CharField(
        max_length=20, choices=VerificationResult.choices, blank=True, default="",
    )
    verification_remarks = models.TextField(blank=True, default="")
    verified_at = models.DateTimeField(null=True, blank=True)

    # ---- CLOSURE ----
    closure_remarks = models.TextField(blank=True, default="")
    closed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
        related_name="closed_bugs", db_column="closed_by",
    )
    closed_date = models.DateField(null=True, blank=True)
    closed_at = models.DateTimeField(null=True, blank=True)

    # ---- REOPEN ----
    reopen_count = models.PositiveIntegerField(default=0)
    last_reopened_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "bug_tracker"
        ordering = ["-id"]
        constraints = [
            # Unconditional: bugs are never hard-deleted (spec 57) and the
            # number generator's correctness depends on absolute uniqueness.
            models.UniqueConstraint(fields=["bug_no"], name="uq_bug_tracker_bug_no"),
        ]
        indexes = [
            # ---- LIST SCREEN (spec 23, the hottest query) ----
            models.Index(fields=["status", "is_deleted"], name="idx_bug_status_deleted"),
            models.Index(fields=["owner", "status"], name="idx_bug_owner_status"),
            models.Index(fields=["project", "status"], name="idx_bug_project_status"),
            models.Index(fields=["module", "status"], name="idx_bug_module_status"),
            models.Index(fields=["priority", "status"], name="idx_bug_priority_status"),
            models.Index(fields=["severity", "status"], name="idx_bug_severity_status"),
            models.Index(fields=["reported_by"], name="idx_bug_reported_by"),
            # ---- OVERDUE (spec 12) ----
            models.Index(fields=["expected_closure_date", "status"], name="idx_bug_expected_status"),
            # ---- AGING (spec 13) AND DAILY REPORT (spec 33) ----
            models.Index(fields=["reported_date"], name="idx_bug_reported_date"),
            models.Index(fields=["closed_date"], name="idx_bug_closed_date"),
            models.Index(fields=["resolved_date"], name="idx_bug_resolved_date"),
            # ---- UPDATE PENDING (spec 11) ----
            models.Index(fields=["status", "latest_update_date", "owner"], name="idx_bug_update_pending"),
            # ---- SEARCH (spec 24, 53) ----
            models.Index(fields=["bug_no"], name="idx_bug_no"),
            models.Index(fields=["title"], name="idx_bug_title"),
            models.Index(fields=["root_cause_type"], name="idx_bug_root_cause_type"),
        ]

    def __str__(self):
        return f"{self.bug_no} - {self.title}"
