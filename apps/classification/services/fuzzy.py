"""Deterministic typo tolerance (spec 17).

This is NOT AI classification. It is edit-distance matching with a configurable
threshold, and it is the only module in the codebase that imports rapidfuzz.

Scorer choice matters and is deliberate:

* fuzz.ratio for single tokens -- plain normalised edit distance.
* fuzz.token_set_ratio for multi-word phrases.
* NOT partial_ratio: it matches any substring window, so comparing "access"
  against "we discussed accessibility" scores about 100 and would raise an
  ACCESS_REQUEST from a word that is not there.
* NOT WRatio: its internal heuristics make a threshold of 88 mean different
  things for different inputs, and spec 17 asks for something explainable.
"""

from rapidfuzz import fuzz, process

from apps.classification.services.normalize import tokenize


def best_token_similarity(normalized_text, pattern, min_length=5):
    """Highest fuzz.ratio between `pattern` and any single token in the text.

    Patterns shorter than `min_length` are skipped entirely: at a threshold of
    88 a four-character pattern tolerates essentially no edits anyway, while
    short patterns produce the worst false positives ("gm" against "grn").
    """
    if not normalized_text or not pattern or len(pattern) < min_length:
        return 0

    tokens = tokenize(normalized_text)
    if not tokens:
        return 0

    match = process.extractOne(pattern, tokens, scorer=fuzz.ratio)
    return int(match[1]) if match else 0


def best_phrase_similarity(normalized_text, pattern, min_length=5):
    """Highest token_set_ratio between `pattern` and any same-length window.

    Windows are built at the pattern's token length and one either side, so
    "not wokring" still matches "not working" despite the typo changing nothing
    about the token count.
    """
    if not normalized_text or not pattern or len(pattern) < min_length:
        return 0

    pattern_tokens = tokenize(pattern)
    text_tokens = tokenize(normalized_text)
    if not pattern_tokens or not text_tokens:
        return 0

    width = len(pattern_tokens)
    windows = []
    for size in {max(1, width - 1), width, width + 1}:
        if size > len(text_tokens):
            continue
        windows.extend(
            " ".join(text_tokens[i:i + size])
            for i in range(len(text_tokens) - size + 1)
        )
    if not windows:
        return 0

    match = process.extractOne(pattern, windows, scorer=fuzz.token_set_ratio)
    return int(match[1]) if match else 0


def similarity(normalized_text, pattern, min_length=5):
    """Dispatch to token or phrase matching based on the pattern's shape."""
    if " " in (pattern or "").strip():
        return best_phrase_similarity(normalized_text, pattern, min_length)
    return best_token_similarity(normalized_text, pattern, min_length)
