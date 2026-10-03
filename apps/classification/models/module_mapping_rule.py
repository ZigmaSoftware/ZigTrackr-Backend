"""Phrase -> project/module/submodule mapping (spec 20)."""

from django.core.validators import MaxValueValidator
from django.db import models

from common.exceptions.domain import SystemRowProtectedError
from common.models import BaseMaster


class ModuleMappingRule(BaseMaster):
    id = models.BigAutoField(primary_key=True)

    keyword_or_phrase = models.CharField(max_length=200)
    normalized_phrase = models.CharField(max_length=200, editable=False, default="")

    project = models.ForeignKey(
        "masters.ProjectMaster", on_delete=models.PROTECT,
        related_name="mapping_rules", db_column="project_id",
    )
    module = models.ForeignKey(
        "masters.ModuleMaster", on_delete=models.PROTECT, null=True, blank=True,
        related_name="mapping_rules", db_column="module_id",
    )
    submodule = models.ForeignKey(
        "masters.SubmoduleMaster", on_delete=models.PROTECT, null=True, blank=True,
        related_name="mapping_rules", db_column="submodule_id",
    )

    score = models.PositiveSmallIntegerField(
        default=100, validators=[MaxValueValidator(100)],
    )
    priority_order = models.PositiveSmallIntegerField(default=100)
    is_system = models.BooleanField(default=False)

    class Meta:
        db_table = "module_mapping_rule"
        # Longer phrases first, so "purchase indent" beats "purchase"
        # deterministically before any tie logic applies.
        ordering = ["priority_order", "-score", "id"]
        # See ClassificationRule: MariaDB ignores conditional unique
        # constraints, so phrase uniqueness among live rows is a serializer
        # check rather than a database guarantee.
        indexes = [
            models.Index(
                fields=["is_active", "is_deleted"], name="idx_maprule_active",
            ),
            models.Index(
                fields=["normalized_phrase", "project"], name="idx_maprule_phrase",
            ),
        ]

    def __str__(self):
        target = " / ".join(
            str(p) for p in (self.project, self.module, self.submodule) if p
        )
        return f"'{self.keyword_or_phrase}' -> {target}"

    def save(self, *args, **kwargs):
        from apps.classification.services.normalize import normalize_text

        self.normalized_phrase = normalize_text(self.keyword_or_phrase)
        return super().save(*args, **kwargs)

    def clean(self):
        """Reject a mapping whose target is not a real Project -> Module -> Submodule path.

        A rule pointing at a submodule of another project files every matching
        email against the wrong project, and the ticket looks correctly routed.
        """
        from django.core.exceptions import ValidationError

        if self.module_id and self.module.project_id != self.project_id:
            raise ValidationError(
                {"module": "Module does not belong to the selected project."}
            )
        if self.submodule_id and self.module_id and self.submodule.module_id != self.module_id:
            raise ValidationError(
                {"submodule": "Submodule does not belong to the selected module."}
            )

    def soft_delete(self, deleted_by=None):
        if self.is_system:
            raise SystemRowProtectedError(
                f"Mapping rule '{self.keyword_or_phrase}' is a system row and cannot be deleted."
            )
        super().soft_delete(deleted_by=deleted_by)
