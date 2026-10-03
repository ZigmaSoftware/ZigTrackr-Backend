"""Data transfer objects crossing the classification boundary (spec 18).

Frozen dataclasses, never ORM models: spec 18 is explicit that raw models must
not be passed between unrelated services, and a frozen object cannot be mutated
by a consumer half-way down the pipeline.

Identifiers are `unique_id` STRINGS rather than integer primary keys. The spec's
example JSON names them project_id, and the serializers keep that name, but the
values are UUIDs -- BaseMaster's dual-identity rule exists precisely to keep
sequential internal ids off the wire.
"""

from dataclasses import dataclass, field
from typing import Mapping


@dataclass(frozen=True)
class MailContext:
    """Everything the classifier is allowed to see about a message.

    Built on the mail_intake side (see services/context.py there). Keeping the
    constructor out of this module is what preserves the one-way dependency:
    mail_intake imports classification, never the reverse.
    """

    subject: str = ""
    body_text: str = ""
    normalized_subject: str = ""
    normalized_body: str = ""
    from_email: str = ""
    to_emails: tuple = ()
    has_attachments: bool = False
    # Correlation only -- deliberately not a model reference.
    mail_unique_id: str = ""


@dataclass(frozen=True)
class RuleMatch:
    """One rule firing, with enough detail to explain the score to a human."""

    rule_unique_id: str
    rule_type: str
    pattern: str
    ticket_type: str
    base_score: int
    matched_in: str        # SUBJECT | BODY
    similarity: int = 100  # 100 for exact matches
    contribution: float = 0.0  # after field weight, fuzzy penalty and decay

    def as_dict(self):
        return {
            "rule_id": self.rule_unique_id,
            "rule_type": self.rule_type,
            "pattern": self.pattern,
            "ticket_type": self.ticket_type,
            "base_score": self.base_score,
            "matched_in": self.matched_in,
            "similarity": self.similarity,
            "contribution": round(self.contribution, 2),
        }


@dataclass(frozen=True)
class ModuleMappingResult:
    project_unique_id: str = ""
    module_unique_id: str = ""
    submodule_unique_id: str = ""
    matched_phrase: str = ""
    score: int = 0
    is_ambiguous: bool = False
    reason: str = ""


@dataclass(frozen=True)
class ClassificationResult:
    """The single shape every classification provider returns."""

    ticket_type: str
    classification_method: str
    classification_score: int
    matched_rules: tuple = ()
    all_scores: Mapping = field(default_factory=dict)

    project_unique_id: str = ""
    module_unique_id: str = ""
    submodule_unique_id: str = ""
    # Reserved so this matches spec 18's shape exactly and Phase 2 needs no DTO
    # change. Always empty in Phase 1: there is no Category master to point at.
    category_unique_id: str = ""

    needs_review: bool = False
    review_reason: str = ""
    provider_name: str = "RULE_BASED"

    @property
    def band(self):
        """AUTO / REVIEW / UNKNOWN, computed once here.

        The frontend must render this rather than re-deriving `score >= 80` in
        TypeScript, which would scatter the threshold the settings deliberately
        centralise.
        """
        if self.ticket_type == "UNKNOWN":
            return "UNKNOWN"
        return "REVIEW" if self.needs_review else "AUTO"

    def as_dict(self):
        return {
            "ticket_type": self.ticket_type,
            "classification_method": self.classification_method,
            "classification_score": self.classification_score,
            "matched_rules": [m.as_dict() for m in self.matched_rules],
            "all_scores": dict(self.all_scores),
            "project_id": self.project_unique_id or None,
            "module_id": self.module_unique_id or None,
            "submodule_id": self.submodule_unique_id or None,
            "category_id": self.category_unique_id or None,
            "needs_review": self.needs_review,
            "review_reason": self.review_reason,
            "band": self.band,
            "provider": self.provider_name,
        }
