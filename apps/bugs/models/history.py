"""Immutable history tables (spec 39.4 - 39.7).

None of these carry is_deleted or updated_at. That is deliberate: a history row
that can be edited or soft-deleted is not history.
"""

from django.conf import settings
from django.db import models

from apps.bugs.constants import BugStatus, TestResult
from common.models import ImmutableHistoryModel


class BugStatusHistory(ImmutableHistoryModel):
    id = models.BigAutoField(primary_key=True)
    bug = models.ForeignKey(
        "bugs.Bug", on_delete=models.PROTECT, related_name="status_history", db_column="bug_id",
    )
    from_status = models.CharField(
        max_length=20, choices=BugStatus.choices, blank=True, default="",
    )
    to_status = models.CharField(max_length=20, choices=BugStatus.choices)
    remarks = models.TextField(blank=True, default="")
    changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
        related_name="status_changes", db_column="changed_by",
    )
    changed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "bug_status_history"
        ordering = ["-changed_at", "-id"]
        indexes = [
            models.Index(fields=["bug", "-changed_at"], name="idx_bugsh_bug_changed"),
            models.Index(fields=["to_status", "changed_at"], name="idx_bugsh_to_status"),
        ]


class BugAssignmentHistory(ImmutableHistoryModel):
    id = models.BigAutoField(primary_key=True)
    bug = models.ForeignKey(
        "bugs.Bug", on_delete=models.PROTECT, related_name="assignment_history", db_column="bug_id",
    )
    from_owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
        related_name="assignments_from", db_column="from_owner_id",
    )
    to_owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
        related_name="assignments_to", db_column="to_owner_id",
    )
    assigned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
        related_name="assignments_made", db_column="assigned_by",
    )
    assigned_at = models.DateTimeField(auto_now_add=True)
    remarks = models.TextField(blank=True, default="")

    class Meta:
        db_table = "bug_assignment_history"
        ordering = ["-assigned_at", "-id"]
        indexes = [
            models.Index(fields=["bug", "-assigned_at"], name="idx_bugah_bug_assigned"),
            models.Index(fields=["to_owner"], name="idx_bugah_to_owner"),
        ]


class BugTestingHistory(ImmutableHistoryModel):
    id = models.BigAutoField(primary_key=True)
    bug = models.ForeignKey(
        "bugs.Bug", on_delete=models.PROTECT, related_name="testing_history", db_column="bug_id",
    )
    tested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
        related_name="test_records", db_column="tested_by",
    )
    test_result = models.CharField(max_length=20, choices=TestResult.choices)
    test_remarks = models.TextField(blank=True, default="")
    tested_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "bug_testing_history"
        ordering = ["-tested_at", "-id"]
        indexes = [
            models.Index(fields=["bug", "-tested_at"], name="idx_bugth_bug_tested"),
            models.Index(fields=["test_result"], name="idx_bugth_result"),
        ]


class BugReopenHistory(ImmutableHistoryModel):
    id = models.BigAutoField(primary_key=True)
    bug = models.ForeignKey(
        "bugs.Bug", on_delete=models.PROTECT, related_name="reopen_history", db_column="bug_id",
    )
    reopen_reason = models.TextField()
    reopened_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
        related_name="reopened_bugs", db_column="reopened_by",
    )
    reopened_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "bug_reopen_history"
        ordering = ["-reopened_at", "-id"]
        indexes = [
            models.Index(fields=["bug", "-reopened_at"], name="idx_bugrh_bug_reopened"),
            models.Index(fields=["reopened_at"], name="idx_bugrh_reopened_at"),
        ]
