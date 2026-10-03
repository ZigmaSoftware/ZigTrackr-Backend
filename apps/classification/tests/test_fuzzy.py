"""Fuzzy matching behaviour (spec 17, spec 55 Classification block)."""

from django.test import SimpleTestCase

from apps.classification.services.fuzzy import similarity
from apps.classification.services.normalize import normalize_text
from apps.classification.services.rule_classifier import RuleClassifier
from apps.classification.tests.factories import make_context, make_rule, spec_rules, test_config

THRESHOLD = 88


class SpecTypoTests(SimpleTestCase):
    """The four examples spec 17 lists by name."""

    def assert_matches(self, text, pattern):
        score = similarity(normalize_text(text), pattern)
        self.assertGreaterEqual(
            score, THRESHOLD, f"{pattern!r} scored {score} against {text!r}"
        )

    def test_eror_matches_error(self):
        self.assert_matches("there is an eror in the form", "error")

    def test_permision_matches_permission(self):
        self.assert_matches("i need permision to view", "permission")

    def test_acess_matches_access(self):
        self.assert_matches("please give acess to purchase", "access")

    def test_not_wokring_matches_not_working(self):
        self.assert_matches("the page is not wokring today", "not working")


class FalsePositiveTests(SimpleTestCase):
    def test_accessibility_does_not_match_access(self):
        """The reason token_set_ratio is used and partial_ratio is not."""
        score = similarity(normalize_text("we discussed accessibility options"), "access")
        self.assertLess(score, THRESHOLD)

    def test_short_patterns_are_skipped_entirely(self):
        """At 88, a 3-character pattern tolerates no edits and misfires badly."""
        self.assertEqual(similarity(normalize_text("the grm module"), "grn"), 0)

    def test_unrelated_text_scores_low(self):
        score = similarity(normalize_text("nothing relevant here at all"), "error")
        self.assertLess(score, THRESHOLD)

    def test_below_threshold_typo_does_not_classify(self):
        clf = RuleClassifier(
            config=test_config(),
            rules=[make_rule("BUG", "FUZZY_KEYWORD", "error", 80)],
        )
        result = clf.classify(make_context("completely unrelated wording"))
        self.assertEqual(result.ticket_type, "UNKNOWN")


class FuzzyPenaltyTests(SimpleTestCase):
    def test_fuzzy_match_scores_below_exact_match(self):
        """A typo is weaker evidence than the real word, never stronger."""
        exact_clf = RuleClassifier(
            config=test_config(), rules=[make_rule("BUG", "KEYWORD", "error", 80)]
        )
        fuzzy_clf = RuleClassifier(
            config=test_config(), rules=[make_rule("BUG", "FUZZY_KEYWORD", "error", 80)]
        )
        exact = exact_clf.classify(make_context("an error today")).classification_score
        typo = fuzzy_clf.classify(make_context("an eror today")).classification_score
        self.assertGreater(exact, typo)


class AccessFuzzyGuardTests(SimpleTestCase):
    """Spec 17: fuzzy matching alone must not auto-create an access request."""

    def test_fuzzy_only_access_is_forced_to_review(self):
        clf = RuleClassifier(
            config=test_config(),
            rules=[make_rule("ACCESS_REQUEST", "FUZZY_KEYWORD", "permission", 100)],
        )
        result = clf.classify(make_context("i need permision urgently"))
        self.assertEqual(result.ticket_type, "ACCESS_REQUEST")
        self.assertTrue(result.needs_review)
        self.assertIn("approximate", result.review_reason.lower())

    def test_guard_holds_even_at_maximum_score(self):
        """Implemented categorically, so a long body cannot overcome it."""
        clf = RuleClassifier(
            config=test_config(),
            rules=[
                make_rule("ACCESS_REQUEST", "FUZZY_KEYWORD", "permission", 100),
                make_rule("ACCESS_REQUEST", "FUZZY_KEYWORD", "access", 100),
            ],
        )
        result = clf.classify(make_context("permision acess", "permision acess " * 20))
        self.assertEqual(result.classification_score, 100)
        self.assertTrue(result.needs_review)

    def test_one_exact_hit_lifts_the_guard(self):
        """An exact match is the 'stronger context' spec 17 asks for."""
        clf = RuleClassifier(
            config=test_config(),
            rules=[
                make_rule("ACCESS_REQUEST", "EXACT_PHRASE", "provide access", 100),
                make_rule("ACCESS_REQUEST", "FUZZY_KEYWORD", "permission", 90),
            ],
        )
        result = clf.classify(make_context("please provide access and permision"))
        self.assertEqual(result.ticket_type, "ACCESS_REQUEST")
        self.assertFalse(result.needs_review)

    def test_bug_is_not_subject_to_the_access_guard(self):
        clf = RuleClassifier(
            config=test_config(),
            rules=[make_rule("BUG", "FUZZY_KEYWORD", "error", 100)],
        )
        result = clf.classify(make_context("an eror occurred"))
        self.assertEqual(result.ticket_type, "BUG")
        self.assertFalse(result.needs_review)
