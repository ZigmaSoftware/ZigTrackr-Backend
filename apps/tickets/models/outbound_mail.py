"""Durable requester email jobs, independent of the Redis broker."""

from django.db import models


class OutboundMail(models.Model):
    class Status(models.TextChoices):
        PENDING = "PENDING"
        SENDING = "SENDING"
        RETRY = "RETRY"
        SENT = "SENT"
        FAILED = "FAILED"

    ticket = models.ForeignKey("tickets.SupportTicket", on_delete=models.PROTECT, related_name="outbound_mail")
    event_key = models.CharField(max_length=160, unique=True)
    kind = models.CharField(max_length=16)
    from_email = models.EmailField(max_length=320)
    recipient = models.EmailField(max_length=320)
    subject = models.CharField(max_length=255)
    body = models.TextField()
    message_id = models.CharField(max_length=255)
    in_reply_to = models.CharField(max_length=255, blank=True, default="")
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    attempts = models.PositiveSmallIntegerField(default=0)
    next_attempt_at = models.DateTimeField(null=True, blank=True)
    claimed_at = models.DateTimeField(null=True, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    last_error = models.CharField(max_length=500, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "ticket_outbound_mail"
        indexes = [models.Index(fields=["status", "next_attempt_at"], name="idx_outmail_due")]
