"""Classification audit trail (spec 33).

Append-only, and deliberately NOT a BaseMaster: a soft-deletable audit row is a
contradiction. One row is written when the engine classifies a mail, and the same
row is updated once when a human confirms or corrects it -- so the pair
(rule prediction, human final) sits on a single row and the accuracy report in
spec 49 is one query.

Spec 33 calls this training data. It is not called that in the UI, because no
model is trained from it yet; it is classification audit and history.
"""

import uuid

from django.conf import settings
from django.db import models

from apps.tickets.constants import TicketType


class ClassificationAudit(models.Model):
    id = models.BigAutoField(primary_key=True)
    unique_id = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)

    mail = models.ForeignKey(
        "mail_intake.MailIntake", on_delete=models.PROTECT,
        related_name="classification_audits", db_column="mail_intake_id",
    )
    ticket = models.ForeignKey(
        "tickets.SupportTicket", on_delete=models.PROTECT, null=True, blank=True,
        related_name="classification_audits", db_column="ticket_id",
    )

    # Truncated copies. This table grows one row per email forever, and the full
    # text already lives on mail_intake.
    original_subject = models.CharField(max_length=255, blank=True, default="")
    original_body = models.TextField(blank=True, default="")

    # ---- WHAT THE RULES PREDICTED ----
    rule_predicted_type = models.CharField(
        max_length=20, choices=TicketType.choices, blank=True, default="",
    )
    rule_score = models.PositiveSmallIntegerField(null=True, blank=True)
    matched_rules = models.JSONField(default=list, blank=True)
    all_scores = models.JSONField(default=dict, blank=True)
    rule_project = models.ForeignKey(
        "masters.ProjectMaster", on_delete=models.PROTECT, null=True, blank=True,
        related_name="+", db_column="rule_project_id",
    )
    rule_module = models.ForeignKey(
        "masters.ModuleMaster", on_delete=models.PROTECT, null=True, blank=True,
        related_name="+", db_column="rule_module_id",
    )
    review_reason = models.CharField(max_length=255, blank=True, default="")

    # ---- WHAT THE HUMAN DECIDED (filled in on confirm) ----
    human_final_type = models.CharField(
        max_length=20, choices=TicketType.choices, blank=True, default="",
    )
    human_final_project = models.ForeignKey(
        "masters.ProjectMaster", on_delete=models.PROTECT, null=True, blank=True,
        related_name="+", db_column="human_final_project_id",
    )
    human_final_module = models.ForeignKey(
        "masters.ModuleMaster", on_delete=models.PROTECT, null=True, blank=True,
        related_name="+", db_column="human_final_module_id",
    )
    corrected_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="classification_corrections", db_column="corrected_by",
    )
    corrected_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "classification_audit"
        ordering = ["-id"]
        indexes = [
            # This pair IS the classification accuracy report.
            models.Index(
                fields=["rule_predicted_type", "human_final_type"],
                name="idx_clsaudit_accuracy",
            ),
            models.Index(fields=["mail"], name="idx_clsaudit_mail"),
            models.Index(fields=["created_at"], name="idx_clsaudit_created"),
        ]

    def __str__(self):
        return f"{self.rule_predicted_type or 'UNKNOWN'} -> {self.human_final_type or '(pending)'}"

    @property
    def was_corrected(self):
        return bool(self.human_final_type) and self.human_final_type != self.rule_predicted_type
