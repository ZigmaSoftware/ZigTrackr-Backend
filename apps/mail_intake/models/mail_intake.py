"""The MailIntake model (spec 23).

Deliberately a plain models.Model rather than a BaseMaster. A received email is
not a master row: it is never soft-deleted, never "activated", and has no
created_by, because nobody in this system created it. BugAttachment sets the same
precedent of declaring id and unique_id explicitly instead.
"""

import uuid

from django.db import models

from apps.mail_intake.constants import MailErrorCode, MailProcessingStatus


class MailIntake(models.Model):
    id = models.BigAutoField(primary_key=True)
    unique_id = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)

    # ---- PROVIDER IDENTIFIERS ----
    provider_message_id = models.CharField(max_length=255, blank=True, default="")
    provider_thread_id = models.CharField(max_length=255, blank=True, default="")

    # ---- RFC HEADERS ----
    # Stored WITHOUT angle brackets, matching SupportTicket.ack_message_id, so a
    # thread lookup can compare the two columns directly. Indexed but NOT unique:
    # see dedupe_key below.
    message_id = models.CharField(max_length=255, blank=True, default="")
    in_reply_to = models.CharField(max_length=255, blank=True, default="")
    # Raw, as received. A long thread's References header runs to several KB, and
    # keeping it byte-for-byte preserves the evidence; parsing happens at read time.
    references_header = models.TextField(blank=True, default="")

    # The actual de-duplication key, and the concurrency guard behind it.
    #
    # Spec 23 asks for "message_id nullable UNIQUE", and spec 42 wants that
    # uniqueness to stop two workers processing one email. On MariaDB those two
    # requirements are incompatible: NULLs are distinct in a unique index (the
    # same behaviour User.soft_delete() documents and relies on), so every
    # header-less message would insert happily alongside its own duplicates.
    #
    # Instead this column is NOT NULL and always set -- a hash of the Message-ID
    # when one exists, otherwise a fingerprint over sender, minute, subject and
    # body. One column, one code path, race-free by construction.
    dedupe_key = models.CharField(max_length=64)

    # ---- ADDRESSES ----
    from_email = models.CharField(max_length=320)
    from_name = models.CharField(max_length=150, blank=True, default="")
    # JSON because these are genuinely lists; audit_log.metadata is the existing
    # precedent that JSONField works on this MariaDB deployment. Not indexed.
    to_emails = models.JSONField(default=list, blank=True)
    cc_emails = models.JSONField(default=list, blank=True)
    reply_to_email = models.CharField(max_length=320, blank=True, default="")

    # ---- CONTENT ----
    subject = models.CharField(max_length=500, blank=True, default="")
    body_text = models.TextField(blank=True, default="")
    # Raw HTML exactly as received. NEVER serialise this to an API response --
    # it is attacker-controlled markup. body_html_sanitized is what may be shown.
    body_html = models.TextField(blank=True, default="")
    body_html_sanitized = models.TextField(blank=True, default="")
    normalized_subject = models.CharField(max_length=500, blank=True, default="")
    normalized_body = models.TextField(blank=True, default="")
    body_hash = models.CharField(max_length=64, blank=True, default="")
    raw_size_bytes = models.PositiveBigIntegerField(default=0)

    # ---- DELIVERY CONTEXT ----
    received_at = models.DateTimeField()
    mailbox_folder = models.CharField(max_length=100, default="INBOX")
    mailbox_address = models.CharField(max_length=320, blank=True, default="")

    # ---- PROCESSING STATE ----
    processing_status = models.CharField(
        max_length=30, choices=MailProcessingStatus.choices,
        default=MailProcessingStatus.RECEIVED,
    )
    processing_attempts = models.PositiveSmallIntegerField(default=0)
    last_error_code = models.CharField(
        max_length=50, choices=MailErrorCode.choices, blank=True, default="",
    )
    last_error_message = models.TextField(blank=True, default="")
    last_attempt_at = models.DateTimeField(null=True, blank=True)
    processed_at = models.DateTimeField(null=True, blank=True)

    linked_ticket = models.ForeignKey(
        "tickets.SupportTicket", on_delete=models.PROTECT, null=True, blank=True,
        related_name="mails", db_column="linked_ticket_id",
    )

    # ---- CLASSIFICATION FLAGS ----
    is_auto_reply = models.BooleanField(default=False)
    is_bounce = models.BooleanField(default=False)
    is_duplicate = models.BooleanField(default=False)
    is_thread_reply = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "mail_intake"
        ordering = ["-received_at", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["dedupe_key"], name="uq_mail_intake_dedupe_key"
            ),
        ]
        indexes = [
            models.Index(
                fields=["processing_status", "received_at"],
                name="idx_mail_status_recvd",
            ),
            # The In-Reply-To lookup in thread detection reads this.
            models.Index(fields=["message_id"], name="idx_mail_message_id"),
            models.Index(fields=["in_reply_to"], name="idx_mail_in_reply_to"),
            models.Index(fields=["provider_thread_id"], name="idx_mail_thread_id"),
            models.Index(fields=["from_email", "received_at"], name="idx_mail_from_recvd"),
            models.Index(fields=["linked_ticket"], name="idx_mail_linked_ticket"),
            models.Index(fields=["received_at"], name="idx_mail_received_at"),
        ]

    def __str__(self):
        return f"{self.from_email}: {self.subject[:60]}"
