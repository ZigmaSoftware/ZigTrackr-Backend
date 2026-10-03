"""Daily update (spec 39.2) -- immutable."""

from django.conf import settings
from django.db import models

from apps.bugs.constants import BugStatus
from common.models import ImmutableHistoryModel


class BugUpdate(ImmutableHistoryModel):
    id = models.BigAutoField(primary_key=True)
    bug = models.ForeignKey(
        "bugs.Bug", on_delete=models.PROTECT, related_name="updates", db_column="bug_id",
    )

    # Status and owner as they stood when the update was written -- a snapshot,
    # not a live read, so history stays truthful after later reassignment.
    status = models.CharField(max_length=20, choices=BugStatus.choices)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
        related_name="bug_updates_as_owner", db_column="owner_id",
    )

    update_text = models.TextField()
    remarks = models.TextField(blank=True, default="")
    next_action = models.CharField(max_length=255, blank=True, default="")
    expected_completion_date = models.DateField(null=True, blank=True)

    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
        related_name="authored_bug_updates", db_column="updated_by",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    update_date = models.DateField(db_index=True)

    # Updates written automatically alongside a status change. The team's rule
    # is that these satisfy the daily-update requirement; the flag exists so
    # that can be tightened later without a migration.
    is_system_generated = models.BooleanField(default=False)

    class Meta:
        db_table = "bug_updates"
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["bug", "-created_at"], name="idx_bugupd_bug_created"),
            models.Index(fields=["update_date", "updated_by"], name="idx_bugupd_date_user"),
            models.Index(fields=["bug", "update_date"], name="idx_bugupd_bug_date"),
        ]

    def __str__(self):
        return f"{self.bug_id}@{self.update_date}"
