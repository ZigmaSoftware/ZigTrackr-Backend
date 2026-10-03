"""Phrase -> project/module/submodule mapping (spec 20).

Exact phrase matching only, deliberately: no fuzzy. "grn" against "grm" at 88
similarity is a coin flip, and a WRONG module is worse than no module, because
the confirm dialog pre-fills it and a reviewer rubber-stamps what they are shown.
An empty mapping asks the question; a wrong one answers it incorrectly.
"""

from apps.classification.config import ClassificationConfig
from apps.classification.constants import MatchField
from apps.classification.dto import ModuleMappingResult
from apps.classification.services.normalize import contains_phrase, normalize_text


class ModuleMapper:
    def __init__(self, config=None, rules=None):
        self.config = config or ClassificationConfig.from_settings()
        self._rules = rules

    def get_rules(self):
        if self._rules is not None:
            return self._rules
        from apps.classification.models import ModuleMappingRule

        return list(
            ModuleMappingRule.objects.filter(is_active=True, is_deleted=False)
            .select_related("project", "module", "submodule")
            .order_by("priority_order", "-score", "id")
        )

    def map(self, normalized_subject, normalized_body):
        rules = self.get_rules()
        if not rules:
            return ModuleMappingResult()

        body = (normalized_body or "")[: self.config.body_char_limit]
        scored = []

        for rule in rules:
            phrase = rule.normalized_phrase or normalize_text(rule.keyword_or_phrase)
            if not phrase:
                continue

            if contains_phrase(normalized_subject or "", phrase):
                weight, field = 1.0, MatchField.SUBJECT
            elif contains_phrase(body, phrase):
                weight, field = self.config.body_field_weight, MatchField.BODY
            else:
                continue

            scored.append((int(round(rule.score * weight)), len(phrase), rule, field))

        if not scored:
            return ModuleMappingResult()

        # Longer phrases win before any tie logic applies, so "purchase indent"
        # beats "purchase" deterministically rather than by row order.
        scored.sort(key=lambda row: (row[0], row[1]), reverse=True)

        best_score, _, best_rule, _ = scored[0]
        if best_score < self.config.module_mapping_min_score:
            return ModuleMappingResult(
                score=best_score,
                reason="No module mapping matched with sufficient confidence.",
            )

        # A tie only matters when the two rules disagree about the destination.
        # Two rules resolving to the same triple is corroboration, not ambiguity.
        best_target = self._target(best_rule)
        for score, _, rule, _ in scored[1:]:
            if best_score - score > self.config.module_mapping_tie_margin:
                break
            if self._target(rule) != best_target:
                return ModuleMappingResult(
                    score=best_score,
                    is_ambiguous=True,
                    reason=(
                        "Multiple module mappings matched "
                        f"('{best_rule.keyword_or_phrase}', '{rule.keyword_or_phrase}')."
                    ),
                )

        return ModuleMappingResult(
            project_unique_id=str(getattr(best_rule.project, "unique_id", "") or ""),
            module_unique_id=str(getattr(best_rule.module, "unique_id", "") or ""),
            submodule_unique_id=str(getattr(best_rule.submodule, "unique_id", "") or ""),
            matched_phrase=best_rule.keyword_or_phrase,
            score=best_score,
        )

    @staticmethod
    def _target(rule):
        return (
            getattr(rule, "project_id", None),
            getattr(rule, "module_id", None),
            getattr(rule, "submodule_id", None),
        )
