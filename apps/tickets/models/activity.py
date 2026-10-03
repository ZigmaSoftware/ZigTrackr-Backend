"""One user-facing event per ticket business action."""

import uuid

from django.conf import settings
from django.db import models


class TicketActivity(models.Model):
    id = models.BigAutoField(primary_key=True)
    unique_id = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    ticket = models.ForeignKey(
        "tickets.SupportTicket", on_delete=models.PROTECT, related_name="activities",
    )
    event_type = models.CharField(max_length=40)
    actor_type = models.CharField(max_length=20, default="SYSTEM")
    actor_user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
    )
    actor_email = models.CharField(max_length=320, blank=True, default="")
    actor_display_name = models.CharField(max_length=150, blank=True, default="")
    title = models.CharField(max_length=150)
    description = models.TextField(blank=True, default="")
    public_description = models.TextField(blank=True, default="")
    metadata = models.JSONField(default=dict, blank=True)
    source_key = models.CharField(max_length=120, unique=True, null=True, blank=True)
    occurred_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "ticket_activity"
        ordering = ["occurred_at", "id"]
        indexes = [models.Index(fields=["ticket", "occurred_at"], name="idx_tact_ticket_time")]


class TicketAssignmentHistory(models.Model):
    id = models.BigAutoField(primary_key=True)
    ticket = models.ForeignKey(
        "tickets.SupportTicket", on_delete=models.PROTECT, related_name="assignment_history",
    )
    from_owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
        related_name="ticket_assignments_from",
    )
    to_owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="ticket_assignments_to",
    )
    performed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="ticket_assignments_made",
    )
    reason = models.TextField(blank=True, default="")
    performed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "ticket_assignment_history"
        ordering = ["-performed_at", "-id"]
        indexes = [models.Index(fields=["ticket", "-performed_at"], name="idx_tassign_ticket_time")]


class TicketReopenHistory(models.Model):
    id = models.BigAutoField(primary_key=True)
    ticket = models.ForeignKey(
        "tickets.SupportTicket", on_delete=models.PROTECT, related_name="reopen_history",
    )
    requester_email = models.CharField(max_length=320)
    reason = models.TextField()
    previous_status = models.CharField(max_length=25)
    reopened_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "ticket_reopen_history"
        ordering = ["-reopened_at", "-id"]
        indexes = [models.Index(fields=["ticket", "-reopened_at"], name="idx_treopen_ticket_time")]
