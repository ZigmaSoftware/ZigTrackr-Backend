"""Test helpers for the classification engine.

Plain functions, matching apps/bugs/tests/factories.py -- no factory_boy.

make_rule() returns a lightweight stand-in rather than a saved model, because
the classifier accepts an injected rule list. Most of the engine is a pure
function of (text, rules, config) and testing it without a database keeps those
tests fast and focused.
"""

from types import SimpleNamespace

from apps.classification.config import ClassificationConfig
from apps.classification.dto import MailContext
from apps.classification.services.normalize import (
    normalize_subject,
    normalize_text,
)

# The fifteen seed rules from spec 16, plus fuzzy variants of the two words the
# spec's typo examples target.
SPEC_RULES = [
    ("BUG", "SUBJECT_PREFIX", "[BUG]", 100),
    ("SERVICE_REQUEST", "SUBJECT_PREFIX", "[SERVICE]", 100),
    ("ACCESS_REQUEST", "SUBJECT_PREFIX", "[ACCESS]", 100),
    ("BUG", "EXACT_PHRASE", "500 error", 100),
    ("BUG", "EXACT_PHRASE", "not working", 85),
    ("BUG", "EXACT_PHRASE", "unable to", 65),
    ("BUG", "KEYWORD", "error", 80),
    ("BUG", "KEYWORD", "bug", 90),
    ("BUG", "KEYWORD", "failed", 75),
    ("ACCESS_REQUEST", "EXACT_PHRASE", "provide access", 100),
    ("ACCESS_REQUEST", "KEYWORD", "permission", 90),
    ("ACCESS_REQUEST", "KEYWORD", "access", 70),
    ("SERVICE_REQUEST", "EXACT_PHRASE", "reset password", 100),
    ("SERVICE_REQUEST", "EXACT_PHRASE", "install software", 100),
    ("SERVICE_REQUEST", "EXACT_PHRASE", "create login", 90),
]

FUZZY_RULES = [
    ("BUG", "FUZZY_KEYWORD", "error", 80),
    ("BUG", "FUZZY_KEYWORD", "not working", 85),
    ("ACCESS_REQUEST", "FUZZY_KEYWORD", "permission", 90),
    ("ACCESS_REQUEST", "FUZZY_KEYWORD", "access", 70),
]


def make_rule(ticket_type, rule_type, pattern, score, priority_order=100, unique_id=None):
    return SimpleNamespace(
        unique_id=unique_id or f"{rule_type}:{pattern}",
        ticket_type=ticket_type,
        rule_type=rule_type,
        pattern=pattern,
        normalized_pattern=normalize_text(pattern),
        score=score,
        priority_order=priority_order,
        is_active=True,
        is_deleted=False,
    )


def spec_rules(include_fuzzy=True):
    rows = list(SPEC_RULES) + (list(FUZZY_RULES) if include_fuzzy else [])
    return [make_rule(*row) for row in rows]


def make_context(subject="", body=""):
    return MailContext(
        subject=subject,
        body_text=body,
        normalized_subject=normalize_subject(subject),
        normalized_body=normalize_text(body),
        from_email="sender@example.com",
    )


def test_config(**overrides):
    """A config with the documented defaults, overridable per test."""
    base = ClassificationConfig()
    if not overrides:
        return base
    return ClassificationConfig(**{**base.__dict__, **overrides})
