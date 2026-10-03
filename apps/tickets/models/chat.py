"""Persisted ticket conversation; sender identity is set by the server."""

import uuid

from django.conf import settings
from django.db import models


class TicketChatMessage(models.Model):
    id = models.BigAutoField(primary_key=True)
    unique_id = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    ticket = models.ForeignKey(
        "tickets.SupportTicket", on_delete=models.PROTECT, related_name="chat_messages",
    )
    sender_type = models.CharField(max_length=20)
    sender_user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
    )
    sender_email = models.CharField(max_length=320, blank=True, default="")
    sender_display_name = models.CharField(max_length=150)
    message_text = models.TextField()
    reply_to_message = models.ForeignKey(
        "self", on_delete=models.PROTECT, null=True, blank=True,
    )
    is_system_message = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    delivered_at = models.DateTimeField(null=True, blank=True)
    read_at = models.DateTimeField(null=True, blank=True)
    is_deleted = models.BooleanField(default=False)
    deleted_at = models.DateTimeField(null=True, blank=True)
    edited_at = models.DateTimeField(null=True, blank=True)
    pinned_at = models.DateTimeField(null=True, blank=True)
    reactions = models.JSONField(default=dict, blank=True)
    starred_by = models.JSONField(default=list, blank=True)
    deleted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
        related_name="hidden_ticket_chat_messages",
    )

    class Meta:
        db_table = "ticket_chat_message"
        ordering = ["created_at", "id"]
        indexes = [models.Index(fields=["ticket", "created_at"], name="idx_tchat_ticket_time")]


class TicketChatRevision(models.Model):
    message = models.ForeignKey(TicketChatMessage, on_delete=models.PROTECT, related_name="revisions")
    previous_text = models.TextField()
    changed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "ticket_chat_revision"
