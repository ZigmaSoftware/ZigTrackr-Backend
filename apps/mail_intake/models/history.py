"""Immutable mail processing history (spec 25).

Extends ImmutableHistoryModel, so an UPDATE or DELETE against a recorded
transition raises rather than quietly rewriting the record.
"""

from django.conf import settings
from django.db import models

from apps.mail_intake.constants import MailErrorCode, MailProcessingStatus
from common.models.immutable import ImmutableHistoryModel


class MailProcessingHistory(ImmutableHistoryModel):
    id = models.BigAutoField(primary_key=True)

    mail = models.ForeignKey(
        "mail_intake.MailIntake", on_delete=models.PROTECT,
        related_name="processing_history", db_column="mail_intake_id",
    )
    # Blank on the opening row, matching BugStatusHistory's convention for
    # "this is where the record begins".
    from_status = models.CharField(
        max_length=30, choices=MailProcessingStatus.choices, blank=True, default="",
    )
    to_status = models.CharField(max_length=30, choices=MailProcessingStatus.choices)
    action = models.CharField(max_length=60)
    remarks = models.TextField(blank=True, default="")
    error_code = models.CharField(
        max_length=50, choices=MailErrorCode.choices, blank=True, default="",
    )

    # Nullable SET_NULL, unlike BugStatusHistory.changed_by which is a non-null
    # PROTECT. Most transitions here are made by the cron process with no user at
    # all, and recording that honestly is exactly what spec 47 asks for: a system
    # action must not impersonate an administrator.
    performed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="mail_history_entries", db_column="performed_by",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "mail_processing_history"
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["mail", "-created_at"], name="idx_mailph_mail_created"),
            models.Index(fields=["to_status", "created_at"], name="idx_mailph_to_status"),
        ]

    def __str__(self):
        return f"{self.from_status or '-'} -> {self.to_status}"
