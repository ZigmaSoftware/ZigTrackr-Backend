"""Classification thresholds as an injectable object.

The engine never reads django.conf.settings directly. Tests build a config with
explicit numbers instead of wrestling override_settings, and a Phase-2 provider
receives the same object without inheriting rule-specific plumbing.
"""

from dataclasses import dataclass

from django.conf import settings


@dataclass(frozen=True)
class ClassificationConfig:
    auto_min_score: int = 80
    review_min_score: int = 50
    # Access is security-sensitive, so it needs a stricter bar (spec 19).
    access_auto_min_score: int = 90
    fuzzy_min_score: int = 88
    # Below this gap between the top two candidates, the result is a conflict
    # rather than a winner.
    conflict_margin: int = 15
    # Each additional corroborating rule contributes half as much as the last.
    score_decay: float = 0.5
    # A word in the body is weaker evidence than a word the sender put in the
    # subject line.
    body_field_weight: float = 0.6
    body_char_limit: int = 4000
    min_fuzzy_pattern_length: int = 5
    module_mapping_min_score: int = 60
    module_mapping_tie_margin: int = 10

    @classmethod
    def from_settings(cls):
        return cls(
            auto_min_score=getattr(settings, "CLASSIFY_AUTO_MIN_SCORE", 80),
            review_min_score=getattr(settings, "CLASSIFY_REVIEW_MIN_SCORE", 50),
            access_auto_min_score=getattr(settings, "ACCESS_AUTO_CLASSIFY_MIN_SCORE", 90),
            fuzzy_min_score=getattr(settings, "FUZZY_MATCH_MIN_SCORE", 88),
            conflict_margin=getattr(settings, "CLASSIFY_CONFLICT_MARGIN", 15),
            score_decay=getattr(settings, "CLASSIFY_SCORE_DECAY", 0.5),
            body_field_weight=getattr(settings, "CLASSIFY_BODY_FIELD_WEIGHT", 0.6),
            body_char_limit=getattr(settings, "CLASSIFY_BODY_CHAR_LIMIT", 4000),
            min_fuzzy_pattern_length=getattr(
                settings, "CLASSIFY_MIN_FUZZY_PATTERN_LENGTH", 5
            ),
            module_mapping_min_score=getattr(settings, "MODULE_MAPPING_MIN_SCORE", 60),
            module_mapping_tie_margin=getattr(settings, "MODULE_MAPPING_TIE_MARGIN", 10),
        )
