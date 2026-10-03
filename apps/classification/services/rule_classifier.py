"""Deterministic rule-based classification (spec 14-19).

Spec 16 gives each rule a score from 0 to 100 and spec 19 wants a confidence
from 0 to 100, but it never says how several matching rules combine. They are
different scales, so the aggregation is defined here:

    hit(rule) = rule.score x field_weight x fuzzy_penalty
    total(T)  = sum over i of  sorted_desc(hits)[i] x decay**i
    score(T)  = min(100, round(total(T)))

Geometric decay, rather than the two obvious alternatives:

* A plain sum reaches 245 for three ordinary keywords. Against a 0-100 scale
  that is meaningless, and it makes every wordy email look certain.
* A maximum throws away corroboration -- which is the only thing that lets a
  conflict be detected at all, and is exactly the "stronger context" spec 17
  demands before a fuzzy access match may be trusted.

Decay keeps the strongest signal at full weight, lets each further signal add
diminishing evidence, and stays naturally bounded.
"""

from collections import defaultdict

from apps.classification.config import ClassificationConfig
from apps.classification.constants import CLASSIFIABLE_TICKET_TYPES, MatchField, RuleType
from apps.classification.dto import ClassificationResult, RuleMatch
from apps.classification.provider import ClassificationProvider
from apps.classification.services import fuzzy
from apps.classification.services.normalize import (
    contains_keyword,
    contains_phrase,
    normalize_text,
)

TICKET_TYPE_UNKNOWN = "UNKNOWN"
METHOD_RULE_BASED = "RULE_BASED"


class RuleClassifier(ClassificationProvider):
    name = METHOD_RULE_BASED

    def __init__(self, config=None, rules=None):
        self.config = config or ClassificationConfig.from_settings()
        # Injectable so tests need no database, and so a caller classifying a
        # batch can load the rule set once.
        self._rules = rules

    # ---- RULE LOADING ----

    def get_rules(self):
        if self._rules is not None:
            return self._rules
        from apps.classification.models import ClassificationRule

        return list(
            ClassificationRule.objects.filter(is_active=True, is_deleted=False)
            .order_by("priority_order", "-score", "id")
        )

    # ---- ENTRY POINT ----

    def classify(self, context):
        rules = self.get_rules()
        subject = context.normalized_subject or normalize_text(context.subject)
        body = (context.normalized_body or normalize_text(context.body_text))[
            : self.config.body_char_limit
        ]

        # Stage 1: an explicit [BUG] / [SERVICE] / [ACCESS] prefix wins outright.
        prefix = self._match_subject_prefix(rules, context.subject)
        if prefix is not None:
            return prefix

        # Stages 2-4: score every type, then pick a winner.
        matches = self._collect_matches(rules, subject, body)
        scores = {t: self._aggregate(matches[t]) for t in CLASSIFIABLE_TICKET_TYPES}
        return self._decide(scores, matches)

    # ---- STAGE 1 ----

    def _match_subject_prefix(self, rules, raw_subject):
        """Handle `[BUG] ...`-style subjects.

        Spec 15 tells users this is how to get deterministic routing, so it must
        be decisive: if a stray body keyword could still drag a [BUG] mail to
        ACCESS_REQUEST, the documented promise to users would be false.
        """
        subject = (raw_subject or "").strip().lower()
        if not subject.startswith("["):
            return None

        hits = []
        for rule in rules:
            if rule.rule_type != RuleType.SUBJECT_PREFIX:
                continue
            token = rule.pattern.strip().lower()
            if not token.startswith("["):
                token = f"[{token.strip('[]')}]"
            if subject.startswith(token):
                hits.append(rule)

        if not hits:
            return None

        distinct_types = {r.ticket_type for r in hits}
        if len(distinct_types) > 1:
            # Two contradictory prefixes in one subject. Rare, and not something
            # to resolve by guessing.
            return ClassificationResult(
                ticket_type=TICKET_TYPE_UNKNOWN,
                classification_method=self.name,
                classification_score=0,
                needs_review=True,
                review_reason=(
                    "Subject carries conflicting prefixes: "
                    + ", ".join(sorted(distinct_types))
                ),
                provider_name=self.name,
            )

        rule = hits[0]
        match = RuleMatch(
            rule_unique_id=str(getattr(rule, "unique_id", "")),
            rule_type=rule.rule_type,
            pattern=rule.pattern,
            ticket_type=rule.ticket_type,
            base_score=rule.score,
            matched_in=MatchField.SUBJECT,
            contribution=100.0,
        )
        return ClassificationResult(
            ticket_type=rule.ticket_type,
            classification_method=self.name,
            classification_score=100,
            matched_rules=(match,),
            all_scores={rule.ticket_type: 100},
            needs_review=False,
            review_reason="",
            provider_name=self.name,
        )

    # ---- STAGES 2-4 ----

    def _collect_matches(self, rules, subject, body):
        """Fire every rule against subject and body, keeping one hit per rule.

        A rule matching in both fields counts once at the subject weight, and a
        keyword repeated through a long body counts once -- otherwise a ranty
        email would win on volume alone.
        """
        matches = defaultdict(list)

        for rule in rules:
            if rule.rule_type == RuleType.SUBJECT_PREFIX:
                continue
            if rule.ticket_type not in CLASSIFIABLE_TICKET_TYPES:
                continue

            pattern = rule.normalized_pattern or normalize_text(rule.pattern)
            if not pattern:
                continue

            hit = self._match_rule(rule, pattern, subject, MatchField.SUBJECT)
            if hit is None:
                hit = self._match_rule(rule, pattern, body, MatchField.BODY)
            if hit is not None:
                matches[rule.ticket_type].append(hit)

        return matches

    def _match_rule(self, rule, pattern, text, field):
        if not text:
            return None

        weight = 1.0 if field == MatchField.SUBJECT else self.config.body_field_weight
        similarity = 100

        if rule.rule_type == RuleType.EXACT_PHRASE:
            if not contains_phrase(text, pattern):
                return None
        elif rule.rule_type == RuleType.KEYWORD:
            if not contains_keyword(text, pattern):
                return None
        elif rule.rule_type == RuleType.FUZZY_KEYWORD:
            similarity = fuzzy.similarity(
                text, pattern, self.config.min_fuzzy_pattern_length
            )
            if similarity < self.config.fuzzy_min_score:
                return None
        else:
            return None

        # A fuzzy hit is worth less than an exact one, proportionally. It never
        # boosts: similarity is capped at 100.
        penalty = similarity / 100 if rule.rule_type == RuleType.FUZZY_KEYWORD else 1.0

        return RuleMatch(
            rule_unique_id=str(getattr(rule, "unique_id", "")),
            rule_type=rule.rule_type,
            pattern=rule.pattern,
            ticket_type=rule.ticket_type,
            base_score=rule.score,
            matched_in=field,
            similarity=similarity,
            contribution=rule.score * weight * penalty,
        )

    def _aggregate(self, type_matches):
        """Saturating sum with geometric decay. See the module docstring."""
        if not type_matches:
            return 0
        ordered = sorted(
            (m.contribution for m in type_matches), reverse=True
        )
        total = sum(
            value * (self.config.score_decay ** index)
            for index, value in enumerate(ordered)
        )
        return min(100, int(round(total)))

    # ---- WINNER SELECTION ----

    def _decide(self, scores, matches):
        ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
        best_type, best_score = ranked[0]
        runner_type, runner_score = ranked[1] if len(ranked) > 1 else ("", 0)

        all_scores = {t: s for t, s in scores.items() if s > 0}

        if best_score < self.config.review_min_score:
            return ClassificationResult(
                ticket_type=TICKET_TYPE_UNKNOWN,
                classification_method=self.name,
                classification_score=best_score,
                matched_rules=tuple(matches.get(best_type, ())),
                all_scores=all_scores,
                needs_review=True,
                review_reason="No rule matched with sufficient confidence.",
                provider_name=self.name,
            )

        # A conflict needs two genuine candidates. A runner-up below the review
        # floor is noise, so 95 against 20 is a clear winner, not a tie.
        margin = best_score - runner_score
        if margin < self.config.conflict_margin and runner_score >= self.config.review_min_score:
            return ClassificationResult(
                ticket_type=TICKET_TYPE_UNKNOWN,
                classification_method=self.name,
                classification_score=best_score,
                matched_rules=tuple(matches.get(best_type, ()) ) + tuple(matches.get(runner_type, ())),
                all_scores=all_scores,
                needs_review=True,
                review_reason=(
                    f"Conflicting classification: {best_type} ({best_score}) "
                    f"vs {runner_type} ({runner_score})."
                ),
                provider_name=self.name,
            )

        winning_matches = tuple(matches.get(best_type, ()))
        threshold = (
            self.config.access_auto_min_score
            if best_type == "ACCESS_REQUEST"
            else self.config.auto_min_score
        )
        needs_review = best_score < threshold
        reason = (
            f"Score {best_score} is below the {threshold} auto-classification threshold."
            if needs_review else ""
        )

        # Spec 17: approximate matching alone must not raise an access request.
        # Implemented categorically rather than as a score penalty -- a penalty
        # can be overcome by a long enough body, which would defeat the rule. A
        # single exact or prefix hit is the "stronger context" that lifts it.
        if best_type == "ACCESS_REQUEST" and winning_matches:
            if all(m.rule_type == RuleType.FUZZY_KEYWORD for m in winning_matches):
                needs_review = True
                reason = "Access request identified only by approximate matching."

        return ClassificationResult(
            ticket_type=best_type,
            classification_method=self.name,
            classification_score=best_score,
            matched_rules=winning_matches,
            all_scores=all_scores,
            needs_review=needs_review,
            review_reason=reason,
            provider_name=self.name,
        )
