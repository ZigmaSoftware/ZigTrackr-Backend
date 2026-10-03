"""Bug attachments (spec 39.3, 31)."""

import uuid

from django.conf import settings
from django.db import models

from apps.bugs.constants import AttachmentContext


class BugAttachment(models.Model):
    id = models.BigAutoField(primary_key=True)
    unique_id = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)

    bug = models.ForeignKey(
        "bugs.Bug", on_delete=models.PROTECT, related_name="attachments", db_column="bug_id",
    )
    bug_update = models.ForeignKey(
        "bugs.BugUpdate", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="attachments", db_column="bug_update_id",
    )
    context = models.CharField(
        max_length=20, choices=AttachmentContext.choices, default=AttachmentContext.BUG,
    )

    # Sanitised original name, shown to users. Never used to build a filesystem
    # path -- spec 31: do not trust user-provided filenames.
    file_name = models.CharField(max_length=255)
    stored_file_name = models.CharField(max_length=255)
    file_path = models.CharField(max_length=500)
    # MIME sniffed from the file's own bytes, not the client's Content-Type.
    file_type = models.CharField(max_length=100)
    file_extension = models.CharField(max_length=10)
    file_size = models.PositiveBigIntegerField()
    checksum_sha256 = models.CharField(max_length=64, blank=True, default="")

    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
        related_name="uploaded_attachments", db_column="uploaded_by",
    )
    uploaded_at = models.DateTimeField(auto_now_add=True)

    is_active = models.BooleanField(default=True)
    is_deleted = models.BooleanField(default=False)
    deleted_at = models.DateTimeField(null=True, blank=True)
    deleted_by = models.UUIDField(null=True, blank=True)

    class Meta:
        db_table = "bug_attachments"
        ordering = ["-uploaded_at", "-id"]
        constraints = [
            models.UniqueConstraint(fields=["stored_file_name"], name="uq_bug_attach_stored_name"),
        ]
        indexes = [
            models.Index(fields=["bug", "is_deleted"], name="idx_bugatt_bug_deleted"),
            models.Index(fields=["bug_update"], name="idx_bugatt_update"),
        ]

    def __str__(self):
        return self.file_name
