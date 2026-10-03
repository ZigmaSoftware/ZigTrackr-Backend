"""Bug number sequence counter (spec 5)."""

from django.db import models


class BugNumberSequence(models.Model):
    """One row per YYMM period, incremented atomically.

    See apps/bugs/services/bug_number.py for the allocation strategy and why a
    MAX(bug_no)+1 scan is not safe here.
    """

    id = models.BigAutoField(primary_key=True)
    period = models.CharField(max_length=4)
    last_number = models.PositiveIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "bug_number_sequence"
        constraints = [
            models.UniqueConstraint(fields=["period"], name="uq_bug_number_sequence_period"),
        ]

    def __str__(self):
        return f"{self.period}:{self.last_number}"
