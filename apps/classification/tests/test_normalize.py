"""Text normalisation (spec 13)."""

from django.test import SimpleTestCase

from apps.classification.services.normalize import (
    contains_keyword,
    contains_phrase,
    normalize_subject,
    normalize_text,
    strip_reply_prefixes,
    tokenize,
)


class NormalizeTextTests(SimpleTestCase):
    def test_lowercases_and_collapses_whitespace(self):
        self.assertEqual(normalize_text("  Hello   WORLD  "), "hello world")

    def test_strips_punctuation(self):
        self.assertEqual(normalize_text("error: the form!!"), "error the form")

    def test_handles_none_and_empty(self):
        self.assertEqual(normalize_text(None), "")
        self.assertEqual(normalize_text(""), "")

    def test_unicode_is_normalised_not_destroyed(self):
        # Accents survive casefolding; only punctuation is stripped.
        self.assertEqual(normalize_text("Café — naïve"), "café naïve")

    def test_is_idempotent(self):
        once = normalize_text("Re: Some  Subject!")
        self.assertEqual(normalize_text(once), once)


class ReplyPrefixTests(SimpleTestCase):
    def test_strips_re(self):
        self.assertEqual(strip_reply_prefixes("Re: Hello"), "Hello")

    def test_strips_numbered_and_repeated_prefixes(self):
        self.assertEqual(strip_reply_prefixes("Re: RE[2]: Fwd: Hello"), "Hello")

    def test_strips_fw_and_fwd(self):
        self.assertEqual(strip_reply_prefixes("FW: Hello"), "Hello")
        self.assertEqual(strip_reply_prefixes("Fwd: Hello"), "Hello")

    def test_leaves_ordinary_subject_alone(self):
        self.assertEqual(strip_reply_prefixes("Report ready"), "Report ready")

    def test_does_not_eat_words_merely_starting_with_re(self):
        self.assertEqual(strip_reply_prefixes("Requesting access"), "Requesting access")

    def test_normalize_subject_combines_both_steps(self):
        self.assertEqual(
            normalize_subject("Re: [BUG] User Creation  Form!"),
            "bug user creation form",
        )


class MatchingHelperTests(SimpleTestCase):
    def test_contains_phrase_is_substring(self):
        self.assertTrue(contains_phrase("the form is not working now", "not working"))
        self.assertFalse(contains_phrase("the form works", "not working"))

    def test_contains_keyword_is_token_bounded(self):
        self.assertTrue(contains_keyword("please provide access now", "access"))
        self.assertFalse(contains_keyword("order accessories today", "access"))

    def test_contains_keyword_matches_at_string_edges(self):
        self.assertTrue(contains_keyword("access", "access"))

    def test_tokenize(self):
        self.assertEqual(tokenize("a b c"), ["a", "b", "c"])
        self.assertEqual(tokenize(""), [])
