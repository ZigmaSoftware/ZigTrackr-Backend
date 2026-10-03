"""Mail normalisation and de-duplication keys (spec 11, 13)."""

import datetime

from django.test import SimpleTestCase

from apps.mail_intake.services.mail_normalizer import (
    clean_readable_body,
    compute_dedupe_key,
    has_meaningful_content,
    html_to_text,
    normalize_body,
    normalize_subject,
    strip_greeting,
    strip_quoted_blocks,
    strip_signature,
)


class SpecExampleTests(SimpleTestCase):
    def test_spec_13_worked_example(self):
        """The exact message spec 13 uses to illustrate normalisation."""
        body = (
            "Hi Team,\n\n"
            "there have bug in user creation form page reclify it. thank you\n\n"
            "Regards,\nSadham\nCoordinator\n"
        )
        self.assertEqual(
            clean_readable_body(body),
            "there have bug in user creation form page reclify it.",
        )
        normalized = normalize_body(body)
        self.assertIn("bug", normalized)
        self.assertIn("user creation form", normalized)
        self.assertIn("reclify", normalized)
        self.assertNotIn("coordinator", normalized)

    def test_greeting_and_inline_courtesy_are_removed(self):
        body = "Hello Support Team,\n\nThe save button fails. Thank you."
        self.assertEqual(clean_readable_body(body), "The save button fails.")

    def test_tabs_and_horizontal_whitespace_are_normalized(self):
        body = "Hi Team,\n\n\tThe\t save  button\t fails.\n\nRegards,\nSupport"
        self.assertEqual(clean_readable_body(body), "The save button fails.")

    def test_html_fallback_is_used_when_plain_part_is_boilerplate(self):
        body = clean_readable_body(
            "Hi Team,\n\nRegards,\nSupport",
            "<p>The <strong>save</strong> button fails.</p>",
        )
        self.assertEqual(body, "The save button fails.")

    def test_boilerplate_only_body_is_not_meaningful(self):
        self.assertFalse(has_meaningful_content("", "Hi Team,\n\nRegards,\nSupport"))
        self.assertFalse(has_meaningful_content("", "Thanks"))

    def test_greeting_in_middle_is_preserved(self):
        body = "The issue is reproducible.\n\nHi, can you check the logs?"
        self.assertEqual(strip_greeting(body), body)

    def test_greeting_with_issue_on_same_line_is_preserved(self):
        body = "Hi, the save button is broken."
        self.assertEqual(clean_readable_body(body), body)


class QuotedBlockTests(SimpleTestCase):
    def test_gmail_style_quote_removed(self):
        text = (
            "My new question here.\n\n"
            "On Thu, 18 Sep 2026 at 10:00, Support <s@x.com> wrote:\n"
            "> the previous message\n> more of it\n"
        )
        result = strip_quoted_blocks(text)
        self.assertIn("My new question here.", result)
        self.assertNotIn("previous message", result)

    def test_outlook_separator_removed(self):
        text = "Reply text.\n\n-----Original Message-----\nFrom: someone\nOld body.\n"
        result = strip_quoted_blocks(text)
        self.assertIn("Reply text.", result)
        self.assertNotIn("Old body.", result)

    def test_plain_quoted_lines_removed(self):
        result = strip_quoted_blocks("New text.\n> quoted line\n> another\n")
        self.assertIn("New text.", result)
        self.assertNotIn("quoted line", result)

    def test_unquoted_text_untouched(self):
        self.assertEqual(strip_quoted_blocks("Just a message."), "Just a message.")


class SignatureTests(SimpleTestCase):
    def test_rfc_delimiter_signature_removed(self):
        result = strip_signature("The message.\n\n-- \nSadham\nCoordinator\n")
        self.assertIn("The message.", result)
        self.assertNotIn("Coordinator", result)

    def test_signoff_block_removed(self):
        result = strip_signature("Please fix this.\n\nRegards,\nSadham\n")
        self.assertIn("Please fix this.", result)
        self.assertNotIn("Sadham", result)

    def test_message_that_is_only_a_signoff_is_empty(self):
        self.assertEqual(strip_signature("Thanks"), "")

    def test_long_trailing_content_is_not_treated_as_signature(self):
        text = "Thanks,\n" + "\n".join(f"detail line {i}" for i in range(10))
        self.assertIn("detail line 9", strip_signature(text))


class HtmlToTextTests(SimpleTestCase):
    def test_tags_stripped(self):
        self.assertEqual(html_to_text("<p>Save <b>failed</b></p>"), "Save failed")

    def test_script_contents_dropped_entirely(self):
        """A regex stripper would emit the JavaScript as body text."""
        result = html_to_text("<div>Hello<script>alert('x')</script></div>")
        self.assertIn("Hello", result)
        self.assertNotIn("alert", result)

    def test_style_contents_dropped(self):
        result = html_to_text("<style>.a{color:red}</style><p>Body</p>")
        self.assertIn("Body", result)
        self.assertNotIn("color", result)

    def test_entities_unescaped(self):
        self.assertIn("A&B", html_to_text("<p>A&amp;B</p>"))

    def test_malformed_html_does_not_raise(self):
        self.assertIsInstance(html_to_text("<p>unclosed <b>bold"), str)

    def test_empty_input(self):
        self.assertEqual(html_to_text(""), "")


class SubjectTests(SimpleTestCase):
    def test_reply_prefixes_stripped(self):
        self.assertEqual(normalize_subject("Re: Fwd: Invoice error"), "invoice error")


class DedupeKeyTests(SimpleTestCase):
    def test_message_id_key_is_case_insensitive(self):
        self.assertEqual(
            compute_dedupe_key(message_id="ABC@x"),
            compute_dedupe_key(message_id="abc@x"),
        )

    def test_message_id_key_is_stable(self):
        self.assertEqual(
            compute_dedupe_key(message_id="a@b"), compute_dedupe_key(message_id="a@b")
        )

    def test_different_message_ids_differ(self):
        self.assertNotEqual(
            compute_dedupe_key(message_id="a@b"), compute_dedupe_key(message_id="c@d")
        )

    def test_fingerprint_used_when_no_message_id(self):
        key = compute_dedupe_key(
            from_email="a@b.com",
            received_at=datetime.datetime(2026, 9, 18, 10, 30, 0),
            normalized_subject="subject",
            body_hash="hash",
        )
        self.assertEqual(len(key), 64)

    def test_fingerprint_collides_within_the_same_minute(self):
        """A re-fetch with a jittered timestamp must still be recognised."""
        base = dict(
            from_email="a@b.com", normalized_subject="s", body_hash="h",
        )
        first = compute_dedupe_key(
            received_at=datetime.datetime(2026, 9, 18, 10, 30, 5), **base
        )
        second = compute_dedupe_key(
            received_at=datetime.datetime(2026, 9, 18, 10, 30, 55), **base
        )
        self.assertEqual(first, second)

    def test_fingerprint_differs_across_minutes(self):
        base = dict(from_email="a@b.com", normalized_subject="s", body_hash="h")
        first = compute_dedupe_key(
            received_at=datetime.datetime(2026, 9, 18, 10, 30, 0), **base
        )
        second = compute_dedupe_key(
            received_at=datetime.datetime(2026, 9, 18, 10, 31, 0), **base
        )
        self.assertNotEqual(first, second)

    def test_fingerprint_differs_by_body(self):
        base = dict(
            from_email="a@b.com",
            received_at=datetime.datetime(2026, 9, 18, 10, 30, 0),
            normalized_subject="s",
        )
        self.assertNotEqual(
            compute_dedupe_key(body_hash="h1", **base),
            compute_dedupe_key(body_hash="h2", **base),
        )

    def test_key_always_fits_the_column(self):
        self.assertEqual(len(compute_dedupe_key(message_id="x" * 500)), 64)
