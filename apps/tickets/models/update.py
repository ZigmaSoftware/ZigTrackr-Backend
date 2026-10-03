"""Ticket-level updates and email replies.

Needed because of the ticket-first design: a reply can arrive before anyone has
confirmed the ticket, so there is no Bug yet and therefore no BugUpdate to
attach it to. Without this table those replies would be silently lost.

Once a ticket has a linked Bug, replies go to BugUpdate via
apps.bugs.services.update_service.add_update() instead -- that service owns the
denormalised latest_* columns the dashboard reads, so nothing else may write
BugUpdate rows.
"""

import uuid

from django.conf import settings
from django.db import models

from apps.tickets.constants import TicketUpdateSource


class TicketUpdate(models.Model):
    id = models.BigAutoField(primary_key=True)
    unique_id = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)

    ticket = models.ForeignKey(
        "tickets.SupportTicket", on_delete=models.PROTECT,
        related_name="updates", db_column="ticket_id",
    )
    update_text = models.TextField()
    remarks = models.TextField(blank=True, default="")
    source = models.CharField(
        max_length=20, choices=TicketUpdateSource.choices,
        default=TicketUpdateSource.USER,
    )
    # Set when this update came from an inbound email rather than a person
    # typing into the UI.
    mail = models.ForeignKey(
        "mail_intake.MailIntake", on_delete=models.PROTECT, null=True, blank=True,
        related_name="ticket_updates", db_column="mail_intake_id",
    )
    # Nullable: a system-generated update has no human author, and saying so is
    # more honest than attributing it to an administrator (spec 47).
    created_by_user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="ticket_updates", db_column="created_by_user",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "ticket_update"
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["ticket", "-created_at"], name="idx_ticketupd_ticket"),
        ]

    def __str__(self):
        return f"{self.ticket_id}: {self.update_text[:50]}"
