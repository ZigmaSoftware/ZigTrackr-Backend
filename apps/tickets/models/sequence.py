"""Ticket number sequence counter.

Mirrors masters.BugNumberSequence. See apps/tickets/services/ticket_number.py
for why a per-period counter row is used rather than MAX(ticket_no)+1.
"""

from django.db import models


class TicketNumberSequence(models.Model):
    id = models.BigAutoField(primary_key=True)
    period = models.CharField(max_length=4)
    last_number = models.PositiveIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "ticket_number_sequence"
        constraints = [
            models.UniqueConstraint(
                fields=["period"], name="uq_ticket_number_sequence_period"
            ),
        ]

    def __str__(self):
        return f"{self.period}:{self.last_number}"


class TicketRefSequence(models.Model):
    """Counter for intake references (REF-YYMM-NNNN).

    Separate from TicketNumberSequence because the two are allocated at
    different moments -- a ref at intake, a number at routing -- and sharing one
    counter would leave both series visibly gappy.
    """

    id = models.BigAutoField(primary_key=True)
    period = models.CharField(max_length=4)
    last_number = models.PositiveIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "ticket_ref_sequence"
        constraints = [
            models.UniqueConstraint(
                fields=["period"], name="uq_ticket_ref_sequence_period"
            ),
        ]

    def __str__(self):
        return f"{self.period}:{self.last_number}"
