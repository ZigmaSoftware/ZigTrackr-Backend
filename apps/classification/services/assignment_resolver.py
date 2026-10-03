"""Assignment rule resolution (spec 26).

Most-specific-first, computed in Python over ONE query rather than five
sequential .filter().first() calls -- five round-trips to express one decision is
five chances to get the NULL semantics subtly different between tiers.

The governing rule is spec 26's last line: never silently select an arbitrary
developer. No match means unassigned.

In Phase 1 this is advisory. Under the ticket-first design nothing is assigned
automatically; the resolution pre-fills the owner field on the confirm dialog.
"""

from dataclasses import dataclass

from django.db.models import Q


@dataclass(frozen=True)
class AssignmentResolution:
    primary_assignee_id: int = None
    backup_assignee_id: int = None
    tester_id: int = None
    rule_unique_id: str = ""
    specificity: int = -1

    @property
    def matched(self):
        return self.rule_unique_id != ""


def _specificity(rule):
    """How narrowly a rule is scoped. Higher wins."""
    return (
        (4 if rule.submodule_id else 0)
        + (2 if rule.module_id else 0)
        + (1 if rule.project_id else 0)
    )


def resolve_assignment(*, ticket_type, project_id=None, module_id=None, submodule_id=None):
    """Return the most specific active rule matching this ticket's scope."""
    from apps.tickets.models import AssignmentRule

    # A NULL column on the rule means "any", so each level matches either the
    # ticket's value or NULL.
    candidates = (
        AssignmentRule.objects.filter(
            ticket_type=ticket_type, is_active=True, is_deleted=False,
        )
        .filter(Q(project_id=project_id) | Q(project__isnull=True))
        .filter(Q(module_id=module_id) | Q(module__isnull=True))
        .filter(Q(submodule_id=submodule_id) | Q(submodule__isnull=True))
        .select_related("primary_assignee", "backup_assignee", "tester")
    )

    best = None
    best_key = None
    for rule in candidates:
        # A rule naming a departed employee must behave as no match, not as an
        # assignment to a dead account.
        if not _is_assignable(rule.primary_assignee):
            continue
        key = (_specificity(rule), -rule.priority_order)
        if best_key is None or key > best_key:
            best, best_key = rule, key

    if best is None:
        return AssignmentResolution()

    return AssignmentResolution(
        primary_assignee_id=best.primary_assignee_id,
        backup_assignee_id=(
            best.backup_assignee_id if _is_assignable(best.backup_assignee) else None
        ),
        tester_id=best.tester_id if _is_assignable(best.tester) else None,
        rule_unique_id=str(best.unique_id),
        specificity=_specificity(best),
    )


def _is_assignable(user):
    if user is None:
        return False
    return bool(getattr(user, "is_active", False)) and not getattr(user, "is_deleted", False)
