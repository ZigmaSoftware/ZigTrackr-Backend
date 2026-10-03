"""Text normalisation shared by rule matching and module mapping.

Matching is done on normalised text only, and rule patterns are normalised once
at save time (ClassificationRule.normalized_pattern), so the two sides always
meet in the same shape.

This module is pure: no Django models, no settings, no I/O.
"""

import re
import unicodedata

# Collapse anything that is not a letter, digit or whitespace into a space.
# Keeping [] out of the stripped set matters: subject prefixes like "[BUG]" are
# matched before normalisation, but a pattern may still legitimately contain one.
_PUNCTUATION = re.compile(r"[^\w\s]", flags=re.UNICODE)
_WHITESPACE = re.compile(r"\s+")

# "Re:", "RE[2]:", "Fwd:", "FW:" and friends, repeated any number of times.
_SUBJECT_PREFIX = re.compile(
    r"^\s*(?:(?:re|fwd?|fw|aw|sv|antw)\s*(?:\[\d+\])?\s*:\s*)+",
    flags=re.IGNORECASE,
)


def normalize_text(value):
    """Lowercase, strip accents and punctuation, collapse whitespace."""
    if not value:
        return ""
    text = unicodedata.normalize("NFKC", str(value)).casefold()
    text = _PUNCTUATION.sub(" ", text)
    return _WHITESPACE.sub(" ", text).strip()


def strip_reply_prefixes(subject):
    """Remove leading Re:/Fwd: markers, preserving the original case."""
    if not subject:
        return ""
    return _SUBJECT_PREFIX.sub("", subject).strip()


def normalize_subject(subject):
    """Reply-prefix-stripped, normalised subject used for matching."""
    return normalize_text(strip_reply_prefixes(subject))


def tokenize(text):
    """Split already-normalised text into tokens."""
    if not text:
        return []
    return text.split(" ")


def contains_phrase(haystack, phrase):
    """Substring match for EXACT_PHRASE rules, on normalised text."""
    if not haystack or not phrase:
        return False
    return phrase in haystack


def contains_keyword(haystack, keyword):
    """Whole-token match for KEYWORD rules.

    Token-bounded rather than substring, so "access" does not fire on
    "accessories" -- the single most common false positive in this rule set.
    """
    if not haystack or not keyword:
        return False
    return re.search(rf"(?<!\w){re.escape(keyword)}(?!\w)", haystack) is not None
