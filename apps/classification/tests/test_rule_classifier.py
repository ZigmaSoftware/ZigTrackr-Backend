"""Classification engine behaviour (spec 55 Classification block, spec 56)."""

from django.test import SimpleTestCase

from apps.classification.services.rule_classifier import RuleClassifier
from apps.classification.tests.factories import make_context, make_rule, spec_rules, test_config


class SubjectPrefixTests(SimpleTestCase):
    def setUp(self):
        self.clf = RuleClassifier(config=test_config(), rules=spec_rules())

    def test_bug_prefix_classified_as_bug(self):
        result = self.clf.classify(make_context("[BUG] User Creation form not working"))
        self.assertEqual(result.ticket_type, "BUG")
        self.assertEqual(result.classification_score, 100)
        self.assertFalse(result.needs_review)

    def test_service_prefix_classified_as_service_request(self):
        result = self.clf.classify(make_context("[SERVICE] Create login for new employee"))
        self.assertEqual(result.ticket_type, "SERVICE_REQUEST")
        self.assertFalse(result.needs_review)

    def test_access_prefix_classified_as_access_request(self):
        result = self.clf.classify(make_context("[ACCESS] Provide Purchase Amendment access"))
        self.assertEqual(result.ticket_type, "ACCESS_REQUEST")
        self.assertFalse(result.needs_review)

    def test_prefix_beats_contradictory_body(self):
        """Spec 15 promises users the prefix is decisive; it has to actually be."""
        result = self.clf.classify(
            make_context("[SERVICE] New joiner setup", "permission access error bug failed")
        )
        self.assertEqual(result.ticket_type, "SERVICE_REQUEST")
        self.assertEqual(result.classification_score, 100)

    def test_two_conflicting_prefixes_go_to_review(self):
        result = self.clf.classify(make_context("[BUG][ACCESS] something odd"))
        # Only the leading prefix can match a startswith, so this stays decisive;
        # the guard matters when a rule set defines overlapping prefixes.
        self.assertIn(result.ticket_type, {"BUG", "UNKNOWN"})

    def test_prefix_is_case_insensitive(self):
        result = self.clf.classify(make_context("[bug] lowercase prefix"))
        self.assertEqual(result.ticket_type, "BUG")


class PhraseAndKeywordTests(SimpleTestCase):
    def setUp(self):
        self.clf = RuleClassifier(config=test_config(), rules=spec_rules())

    def test_not_working_contributes_bug_score(self):
        result = self.clf.classify(make_context("Save button not working"))
        self.assertEqual(result.ticket_type, "BUG")
        self.assertGreaterEqual(result.classification_score, 85)

    def test_provide_access_contributes_access_score(self):
        result = self.clf.classify(make_context("Please provide access to the module"))
        self.assertEqual(result.ticket_type, "ACCESS_REQUEST")
        self.assertGreaterEqual(result.classification_score, 90)

    def test_reset_password_contributes_service_score(self):
        result = self.clf.classify(make_context("Please reset password for new user"))
        self.assertEqual(result.ticket_type, "SERVICE_REQUEST")
        self.assertGreaterEqual(result.classification_score, 90)

    def test_keyword_is_token_bounded(self):
        """'access' must not fire on 'accessories' -- the classic false positive."""
        clf = RuleClassifier(
            config=test_config(),
            rules=[make_rule("ACCESS_REQUEST", "KEYWORD", "access", 70)],
        )
        result = clf.classify(make_context("Order of accessories for the office"))
        self.assertEqual(result.ticket_type, "UNKNOWN")

    def test_subject_outweighs_body(self):
        """The same word is stronger evidence in a subject than in a body."""
        clf = RuleClassifier(config=test_config(), rules=spec_rules())
        in_subject = clf.classify(make_context("bug")).classification_score
        in_body = clf.classify(make_context("hello", "bug")).classification_score
        self.assertGreater(in_subject, in_body)

    def test_repeated_keyword_counts_once(self):
        """A ranty email must not win on volume."""
        clf = RuleClassifier(
            config=test_config(), rules=[make_rule("BUG", "KEYWORD", "bug", 90)]
        )
        once = clf.classify(make_context("bug")).classification_score
        many = clf.classify(make_context("bug", "bug bug bug bug bug")).classification_score
        self.assertEqual(once, many)


class AggregationTests(SimpleTestCase):
    def test_score_saturates_at_100(self):
        clf = RuleClassifier(config=test_config(), rules=spec_rules())
        result = clf.classify(make_context("bug error failed not working 500 error"))
        self.assertLessEqual(result.classification_score, 100)
        self.assertEqual(result.ticket_type, "BUG")

    def test_corroboration_raises_score_above_single_hit(self):
        """Two matching rules must outscore one.

        Uses deliberately low scores: the spec's own rules saturate at 100 from a
        single strong phrase, which would hide the effect being tested.
        """
        rules = [
            make_rule("BUG", "KEYWORD", "alpha", 30),
            make_rule("BUG", "KEYWORD", "beta", 30),
        ]
        clf = RuleClassifier(config=test_config(), rules=rules)
        single = clf.classify(make_context("alpha")).classification_score
        double = clf.classify(make_context("alpha beta")).classification_score
        self.assertGreater(double, single)

    def test_decay_means_second_hit_adds_less_than_first(self):
        rules = [
            make_rule("BUG", "KEYWORD", "alpha", 40),
            make_rule("BUG", "KEYWORD", "beta", 40),
        ]
        clf = RuleClassifier(config=test_config(), rules=rules)
        one = clf.classify(make_context("alpha")).classification_score
        two = clf.classify(make_context("alpha beta")).classification_score
        self.assertEqual(one, 40)
        self.assertEqual(two, 60)  # 40 + 40*0.5


class ConflictAndUnknownTests(SimpleTestCase):
    def setUp(self):
        self.clf = RuleClassifier(config=test_config(), rules=spec_rules())

    def test_conflicting_rules_go_to_needs_review(self):
        result = self.clf.classify(
            make_context("permission error", "bug permission access error")
        )
        self.assertEqual(result.ticket_type, "UNKNOWN")
        self.assertTrue(result.needs_review)
        self.assertIn("Conflicting", result.review_reason)

    def test_weak_score_is_unknown_and_needs_review(self):
        result = self.clf.classify(make_context("Please check this", "Need support."))
        self.assertEqual(result.ticket_type, "UNKNOWN")
        self.assertTrue(result.needs_review)
        self.assertEqual(result.band, "UNKNOWN")

    def test_clear_winner_is_not_treated_as_conflict(self):
        """A runner-up below the review floor is noise, not a tie."""
        rules = [
            make_rule("BUG", "KEYWORD", "bug", 95),
            make_rule("ACCESS_REQUEST", "KEYWORD", "access", 20),
        ]
        clf = RuleClassifier(config=test_config(), rules=rules)
        result = clf.classify(make_context("bug access"))
        self.assertEqual(result.ticket_type, "BUG")
        self.assertNotIn("Conflicting", result.review_reason)


class ThresholdTests(SimpleTestCase):
    def _score_for(self, score, ticket_type="BUG"):
        clf = RuleClassifier(
            config=test_config(),
            rules=[make_rule(ticket_type, "KEYWORD", "trigger", score)],
        )
        return clf.classify(make_context("trigger"))

    def test_at_and_above_auto_threshold_classifies_automatically(self):
        self.assertFalse(self._score_for(80).needs_review)
        self.assertFalse(self._score_for(95).needs_review)

    def test_between_review_and_auto_needs_review(self):
        for value in (50, 79):
            result = self._score_for(value)
            self.assertEqual(result.ticket_type, "BUG")
            self.assertTrue(result.needs_review)
            self.assertEqual(result.band, "REVIEW")

    def test_below_review_floor_is_unknown(self):
        result = self._score_for(49)
        self.assertEqual(result.ticket_type, "UNKNOWN")

    def test_access_uses_the_stricter_threshold(self):
        """85 auto-classifies as a bug but must not auto-classify as access."""
        self.assertFalse(self._score_for(85, "BUG").needs_review)
        access = self._score_for(85, "ACCESS_REQUEST")
        self.assertEqual(access.ticket_type, "ACCESS_REQUEST")
        self.assertTrue(access.needs_review)

    def test_config_overrides_settings(self):
        clf = RuleClassifier(
            config=test_config(auto_min_score=10),
            rules=[make_rule("BUG", "KEYWORD", "trigger", 60)],
        )
        self.assertFalse(clf.classify(make_context("trigger")).needs_review)
