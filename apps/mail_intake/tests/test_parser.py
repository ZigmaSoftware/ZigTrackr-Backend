"""RFC822 parsing (spec 8)."""

from django.test import SimpleTestCase

from apps.mail_intake.services.mail_parser import (
    parse_message_ids,
    parse_raw_email,
    strip_angle_brackets,
)
from apps.mail_intake.tests.factories import make_parsed, make_raw_email


class HeaderTests(SimpleTestCase):
    def test_parses_addresses_and_subject(self):
        parsed = make_parsed(
            sender="Sadham Hussain <sadham@zigmaglobal.in>",
            to="intake@zigma.in",
            cc="lead@zigma.in, qa@zigma.in",
            subject="User creation issue",
        )
        self.assertEqual(parsed.from_email, "sadham@zigmaglobal.in")
        self.assertEqual(parsed.from_name, "Sadham Hussain")
        self.assertEqual(parsed.to_emails, ("intake@zigma.in",))
        self.assertEqual(parsed.cc_emails, ("lead@zigma.in", "qa@zigma.in"))
        self.assertEqual(parsed.subject, "User creation issue")

    def test_addresses_are_lowercased_and_deduplicated(self):
        parsed = make_parsed(to="A@B.com, a@b.com")
        self.assertEqual(parsed.to_emails, ("a@b.com",))

    def test_decodes_rfc2047_encoded_subject(self):
        raw = make_raw_email(subject="[BUG] User Creation form not working")
        # Force the encoded-word form a real client would send.
        encoded = raw.replace(
            b"Subject: [BUG] User Creation form not working",
            b"Subject: =?UTF-8?B?W0JVR10gVXNlciBDcmVhdGlvbiBmb3JtIG5vdCB3b3JraW5n?=",
        )
        parsed = parse_raw_email(encoded)
        self.assertEqual(parsed.subject, "[BUG] User Creation form not working")

    def test_parses_date_header(self):
        parsed = make_parsed(date="Thu, 18 Sep 2026 10:30:00 +0530")
        self.assertEqual(parsed.received_at.year, 2026)
        self.assertEqual(parsed.received_at.month, 9)
        self.assertFalse(parsed.date_header_missing)

    def test_missing_date_header_falls_back_to_now(self):
        parsed = make_parsed(date=None)
        self.assertIsNotNone(parsed.received_at)
        self.assertTrue(parsed.date_header_missing)

    def test_unparseable_date_falls_back_to_now(self):
        parsed = make_parsed(date="not a date at all")
        self.assertIsNotNone(parsed.received_at)
        self.assertTrue(parsed.date_header_missing)


class MessageIdTests(SimpleTestCase):
    def test_angle_brackets_stripped(self):
        parsed = make_parsed(message_id="<ABC123@mail.gmail.com>")
        self.assertEqual(parsed.message_id, "ABC123@mail.gmail.com")

    def test_in_reply_to_stripped_consistently(self):
        parsed = make_parsed(in_reply_to="<PARENT@mail.gmail.com>")
        self.assertEqual(parsed.in_reply_to, "PARENT@mail.gmail.com")

    def test_strip_angle_brackets_helper(self):
        self.assertEqual(strip_angle_brackets("  <x@y>  "), "x@y")
        self.assertEqual(strip_angle_brackets(""), "")
        self.assertEqual(strip_angle_brackets(None), "")

    def test_references_parsed_into_tokens(self):
        parsed = make_parsed(references="<R1@x> <R2@x> <R3@x>")
        self.assertEqual(parse_message_ids(parsed.references_header), ["R1@x", "R2@x", "R3@x"])

    def test_references_limit_keeps_the_tail(self):
        """The immediate parents are the useful ones on a long thread."""
        header = " ".join(f"<R{i}@x>" for i in range(30))
        self.assertEqual(parse_message_ids(header, limit=3), ["R27@x", "R28@x", "R29@x"])

    def test_missing_message_id_is_empty_not_none(self):
        parsed = make_parsed(message_id=None)
        self.assertEqual(parsed.message_id, "")


class BodyTests(SimpleTestCase):
    def test_prefers_text_plain(self):
        parsed = make_parsed(body="plain version", html_body="<p>html version</p>")
        self.assertEqual(parsed.body_text, "plain version")
        self.assertIn("html version", parsed.body_html)

    def test_html_only_mail_still_yields_html(self):
        raw = make_raw_email(body="", html_body="<p>only html</p>")
        parsed = parse_raw_email(raw)
        self.assertIn("only html", parsed.body_html)


class AttachmentTests(SimpleTestCase):
    def test_extracts_attachment_with_sniffable_bytes(self):
        parsed = make_parsed(
            attachments=[("shot.png", b"\x89PNG\r\n\x1a\n data", "image", "png")]
        )
        self.assertEqual(len(parsed.attachments), 1)
        attachment = parsed.attachments[0]
        self.assertEqual(attachment.file_name, "shot.png")
        self.assertEqual(attachment.content_type, "image/png")
        self.assertTrue(attachment.content.startswith(b"\x89PNG"))

    def test_no_attachments_is_empty_tuple(self):
        self.assertEqual(make_parsed().attachments, ())

    def test_raw_size_is_recorded(self):
        parsed = make_parsed(body="x" * 200)
        self.assertGreater(parsed.raw_size_bytes, 200)
