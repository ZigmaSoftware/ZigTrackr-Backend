"""Configurable assignment rules (spec 26).

Spec 7 sketches a separate `assignments` app for this one table. It lives here
instead: it is a small master consumed only by ticket intake, and a whole Django
app per model is more structure than that earns.

The governing rule is spec 26's last line -- never silently select an arbitrary
developer. No matching rule means unassigned, which a human then triages. An
auto-assignment to the wrong person is worse than no assignment, because it looks
deliberate.
"""

from django.conf import settings
from django.db import models

from apps.tickets.constants import TicketType
from common.exceptions.domain import SystemRowProtectedError
from common.models import BaseMaster


class AssignmentRule(BaseMaster):
    id = models.BigAutoField(primary_key=True)

    ticket_type = models.CharField(max_length=20, choices=TicketType.choices)

    # All nullable: NULL means "any", which is what makes a rule general. The
    # resolver ranks by how many of these are set (specificity).
    project = models.ForeignKey(
        "masters.ProjectMaster", on_delete=models.PROTECT, null=True, blank=True,
        related_name="assignment_rules", db_column="project_id",
    )
    module = models.ForeignKey(
        "masters.ModuleMaster", on_delete=models.PROTECT, null=True, blank=True,
        related_name="assignment_rules", db_column="module_id",
    )
    submodule = models.ForeignKey(
        "masters.SubmoduleMaster", on_delete=models.PROTECT, null=True, blank=True,
        related_name="assignment_rules", db_column="submodule_id",
    )

    primary_assignee = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
        related_name="primary_assignment_rules", db_column="primary_assignee_id",
    )
    backup_assignee = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
        related_name="backup_assignment_rules", db_column="backup_assignee_id",
    )
    tester = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
        related_name="tester_assignment_rules", db_column="tester_id",
    )

    priority_order = models.PositiveSmallIntegerField(default=100)
    is_system = models.BooleanField(default=False)

    class Meta:
        db_table = "assignment_rule"
        ordering = ["priority_order", "id"]
        indexes = [
            models.Index(
                fields=["ticket_type", "is_active", "is_deleted"],
                name="idx_assignrule_type",
            ),
            models.Index(
                fields=["project", "module", "submodule"],
                name="idx_assignrule_scope",
            ),
        ]

    def __str__(self):
        scope = " / ".join(
            str(part) for part in (self.project, self.module, self.submodule) if part
        )
        return f"{self.ticket_type}: {scope or 'default'}"

    def clean(self):
        """Reject a rule whose scope is not a real Project -> Module -> Submodule path.

        A rule pointing at a submodule of a different project would route tickets
        to the wrong team, and the damage is invisible: the ticket looks properly
        assigned.
        """
        from django.core.exceptions import ValidationError

        if self.module_id and self.project_id and self.module.project_id != self.project_id:
            raise ValidationError(
                {"module": "Module does not belong to the selected project."}
            )
        if self.submodule_id and self.module_id and self.submodule.module_id != self.module_id:
            raise ValidationError(
                {"submodule": "Submodule does not belong to the selected module."}
            )

    def soft_delete(self, deleted_by=None):
        # Enforced on the model rather than the serializer: a serializer guard
        # protects one API path, and a future bulk operation or shell session
        # would quietly bypass it.
        if self.is_system:
            raise SystemRowProtectedError(
                "This assignment rule is a system row and cannot be deleted."
            )
        super().soft_delete(deleted_by=deleted_by)
