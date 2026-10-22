"""Short-lived email passcodes for the public tracking page.

A plain Model rather than one of the common/ bases: soft delete is wrong for
rows meant to be purged once used, and ImmutableHistoryModel forbids the
`attempts += 1` update this flow depends on. OutboundMail is the precedent.

The code itself is never stored. Only a keyed hash is kept, so a leaked
database row does not grant access to anyone's ticket.
"""

import uuid

from django.db import models


class TicketAccessOtp(models.Model):
    unique_id = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    ticket = models.ForeignKey(
        "tickets.SupportTicket", on_delete=models.CASCADE, related_name="access_otps",
    )
    # Stored lowercase. The ticket's own column is not normalised (manual
    # creation copies the reporter address verbatim), so this is the
    # normalised copy that lookups compare against.
    email = models.CharField(max_length=320)
    code_hash = models.CharField(max_length=128)
    expires_at = models.DateTimeField()
    attempts = models.PositiveSmallIntegerField(default=0)
    consumed_at = models.DateTimeField(null=True, blank=True)
    request_ip = models.GenericIPAddressField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "ticket_access_otp"
        indexes = [
            models.Index(
                fields=["ticket", "email", "consumed_at"], name="idx_ticket_otp_lookup",
            ),
            models.Index(fields=["expires_at"], name="idx_ticket_otp_expiry"),
        ]

    def __str__(self):
        return f"OTP for ticket {self.ticket_id} ({self.email})"
