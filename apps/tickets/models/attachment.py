"""Ticket attachments.

A near-copy of bugs.BugAttachment: same storage discipline (a stored name that
shares nothing with the uploaded one, MIME sniffed from the bytes, soft delete
so the audit trail keeps its evidence). The one addition is `reason`, which is
required here -- a file on a ticket is evidence for a claim, and the uploader
says what it shows rather than leaving the next reader to guess.
"""

import uuid

from django.conf import settings
from django.db import models


class TicketAttachment(models.Model):
    id = models.BigAutoField(primary_key=True)
    unique_id = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)

    ticket = models.ForeignKey(
        "tickets.SupportTicket", on_delete=models.PROTECT,
        related_name="attachments", db_column="ticket_id",
    )

    # Why this file is here. Required at the API boundary; the column allows
    # blank so a future system-generated attachment is not forced to invent one.
    reason = models.CharField(max_length=255, blank=True, default="")

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
        related_name="uploaded_ticket_attachments", db_column="uploaded_by",
    )
    uploaded_at = models.DateTimeField(auto_now_add=True)

    is_active = models.BooleanField(default=True)
    is_deleted = models.BooleanField(default=False)
    deleted_at = models.DateTimeField(null=True, blank=True)
    deleted_by = models.UUIDField(null=True, blank=True)

    class Meta:
        db_table = "ticket_attachments"
        ordering = ["-uploaded_at", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["stored_file_name"], name="uq_ticket_attach_stored_name",
            ),
        ]
        indexes = [
            models.Index(fields=["ticket", "is_deleted"], name="idx_tktatt_tkt_deleted"),
        ]

    def __str__(self):
        return self.file_name
