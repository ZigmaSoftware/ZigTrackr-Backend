"""Configurable classification rules (spec 16).

BaseMaster rather than CodedMaster: a rule has a `pattern`, which is not unique
across the table -- "error" is legitimately a BUG keyword scoring 80 and could
also be a SERVICE_REQUEST keyword scoring 30 -- and no stable semantic `code`
that reports filter on. CodedMaster would force a meaningless code and name. The
is_system delete guard is copied across explicitly instead.
"""

from django.core.validators import MaxValueValidator
from django.db import models

from apps.classification.constants import RuleType
from apps.tickets.constants import TicketType
from common.exceptions.domain import SystemRowProtectedError
from common.models import BaseMaster


class ClassificationRule(BaseMaster):
    id = models.BigAutoField(primary_key=True)

    ticket_type = models.CharField(max_length=20, choices=TicketType.choices)
    rule_type = models.CharField(max_length=20, choices=RuleType.choices)
    pattern = models.CharField(max_length=200)
    # Computed in save() so matching never re-normalises every rule per email.
    normalized_pattern = models.CharField(max_length=200, editable=False, default="")

    score = models.PositiveSmallIntegerField(validators=[MaxValueValidator(100)])
    priority_order = models.PositiveSmallIntegerField(default=100)
    is_system = models.BooleanField(default=False)
    notes = models.TextField(blank=True, default="")

    class Meta:
        db_table = "classification_rule"
        ordering = ["priority_order", "-score", "id"]
        # NO UniqueConstraint here, deliberately. Uniqueness must exclude
        # soft-deleted rows (otherwise deleting a rule permanently blocks
        # re-creating the same pattern), and MariaDB silently ignores a
        # conditional unique constraint -- Django emits models.W036 and creates
        # nothing, which is worse than no constraint because the model would
        # claim a guarantee the database does not provide. Enforced in the
        # serializer instead, the same way UniqueNameMixin handles the masters.
        indexes = [
            models.Index(
                fields=["is_active", "is_deleted", "rule_type"],
                name="idx_clsrule_active_type",
            ),
            models.Index(fields=["ticket_type"], name="idx_clsrule_ticket_type"),
            # Non-unique, but makes the serializer's duplicate check an index seek.
            models.Index(
                fields=["ticket_type", "rule_type", "normalized_pattern"],
                name="idx_clsrule_pattern",
            ),
        ]

    def __str__(self):
        return f"{self.rule_type} '{self.pattern}' -> {self.ticket_type} ({self.score})"

    def save(self, *args, **kwargs):
        from apps.classification.services.normalize import normalize_text

        self.normalized_pattern = normalize_text(self.pattern)
        return super().save(*args, **kwargs)

    def soft_delete(self, deleted_by=None):
        # Spec 16: system rules are protected from deletion. They remain
        # DEACTIVATABLE on purpose -- the protection is against losing a seeded
        # rule, not against switching off one that is misrouting live mail.
        if self.is_system:
            raise SystemRowProtectedError(
                f"Classification rule '{self.pattern}' is a system row and cannot be deleted."
            )
        super().soft_delete(deleted_by=deleted_by)
