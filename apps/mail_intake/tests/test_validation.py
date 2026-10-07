"""Mail validation (spec 10, spec 55 Mail Validation block)."""

from django.test import SimpleTestCase

from apps.mail_intake.constants import MailErrorCode
from apps.mail_intake.services.mail_validator import validate_mail
from apps.mail_intake.tests.factories import DEFAULT_MAILBOX, make_parsed


class AcceptanceTests(SimpleTestCase):
    def test_valid_email_accepted(self):
        result = validate_mail(make_parsed())
        self.assertTrue(result.is_valid)
        self.assertEqual(result.error_code, "")

    def test_subject_only_is_enough(self):
        """'[BUG] Invoice screen blank' is a complete report."""
        result = validate_mail(make_parsed(subject="[BUG] Invoice screen blank", body=""))
        self.assertTrue(result.is_valid)

    def test_body_only_is_enough(self):
        result = validate_mail(make_parsed(subject="", body="The save button fails."))
        self.assertTrue(result.is_valid)


class SenderTests(SimpleTestCase):
    def test_missing_sender_rejected(self):
        result = validate_mail(make_parsed(sender=""))
        self.assertFalse(result.is_valid)
        self.assertEqual(result.error_code, MailErrorCode.NO_SENDER)

    def test_malformed_sender_rejected(self):
        result = validate_mail(make_parsed(sender="not-an-address"))
        self.assertFalse(result.is_valid)
        self.assertEqual(result.error_code, MailErrorCode.INVALID_SENDER)

    def test_display_name_form_is_accepted(self):
        result = validate_mail(make_parsed(sender="Sadham H <sadham@zigmaglobal.in>"))
        self.assertTrue(result.is_valid)


class ContentTests(SimpleTestCase):
    def test_empty_subject_and_body_rejected(self):
        result = validate_mail(make_parsed(subject="", body=""))
        self.assertFalse(result.is_valid)
        self.assertEqual(result.error_code, MailErrorCode.EMPTY_CONTENT)

    def test_boilerplate_only_body_rejected(self):
        result = validate_mail(make_parsed(subject="", body="Hi Team,\n\nRegards,\nSupport"))
        self.assertFalse(result.is_valid)
        self.assertEqual(result.error_code, MailErrorCode.EMPTY_CONTENT)

    def test_html_markup_only_is_empty(self):
        result = validate_mail(
            make_parsed(subject="", body="", html_body="<div><br></div>")
        )
        self.assertFalse(result.is_valid)
        self.assertEqual(result.error_code, MailErrorCode.EMPTY_CONTENT)


class UnwantedMailTests(SimpleTestCase):
    def test_promotional_subject_rejected(self):
        result = validate_mail(make_parsed(subject="Newsletter: July offers"))
        self.assertFalse(result.is_valid)
        self.assertEqual(result.error_code, MailErrorCode.PROMOTIONAL_MAIL)

    def test_promotional_list_header_rejected(self):
        result = validate_mail(
            make_parsed(subject="Updates", extra_headers={"List-Unsubscribe": "<mailto:u@x>"})
        )
        self.assertFalse(result.is_valid)
        self.assertEqual(result.error_code, MailErrorCode.PROMOTIONAL_MAIL)

    def test_explicit_ticket_marker_is_not_promotional(self):
        result = validate_mail(make_parsed(subject="[BUG] Newsletter page is broken"))
        self.assertTrue(result.is_valid)

    def test_no_reply_sender_rejected(self):
        result = validate_mail(make_parsed(sender="no-reply@vendor.example"))
        self.assertFalse(result.is_valid)
        self.assertEqual(result.error_code, MailErrorCode.NO_REPLY)

    def test_provider_no_reply_alias_is_rejected(self):
        result = validate_mail(make_parsed(sender="google-noreply@google.com"))
        self.assertFalse(result.is_valid)
        self.assertEqual(result.error_code, MailErrorCode.NO_REPLY)


class RecipientTests(SimpleTestCase):
    def test_recipient_not_intake_mailbox_rejected(self):
        result = validate_mail(
            make_parsed(to="someone.else@example.com"),
            allowed_recipients=[DEFAULT_MAILBOX],
        )
        self.assertFalse(result.is_valid)
        self.assertEqual(result.error_code, MailErrorCode.RECIPIENT_MISMATCH)

    def test_matching_recipient_accepted(self):
        result = validate_mail(make_parsed(), allowed_recipients=[DEFAULT_MAILBOX])
        self.assertTrue(result.is_valid)

    def test_cc_counts_as_a_recipient(self):
        result = validate_mail(
            make_parsed(to="other@example.com", cc=DEFAULT_MAILBOX),
            allowed_recipients=[DEFAULT_MAILBOX],
        )
        self.assertTrue(result.is_valid)

    def test_empty_allowed_list_accepts_everything(self):
        """A misconfigured alias list must not silently reject all mail."""
        result = validate_mail(make_parsed(to="anywhere@example.com"), allowed_recipients=[])
        self.assertTrue(result.is_valid)


class AutoReplyTests(SimpleTestCase):
    def assert_auto_reply(self, **kwargs):
        result = validate_mail(make_parsed(**kwargs))
        self.assertFalse(result.is_valid)
        self.assertEqual(result.error_code, MailErrorCode.AUTO_REPLY)
        self.assertTrue(result.is_auto_reply)

    def test_auto_submitted_header_detected(self):
        self.assert_auto_reply(extra_headers={"Auto-Submitted": "auto-replied"})

    def test_precedence_bulk_detected(self):
        self.assert_auto_reply(extra_headers={"Precedence": "bulk"})

    def test_x_autoreply_header_detected(self):
        self.assert_auto_reply(extra_headers={"X-Autoreply": "yes"})

    def test_out_of_office_subject_detected(self):
        self.assert_auto_reply(subject="Out of Office: your request")

    def test_automatic_reply_subject_detected(self):
        self.assert_auto_reply(subject="Automatic reply: ticket received")

    def test_auto_submitted_no_is_ordinary_mail(self):
        """'no' is the explicit marker for human-sent mail."""
        result = validate_mail(make_parsed(extra_headers={"Auto-Submitted": "no"}))
        self.assertTrue(result.is_valid)


class BounceTests(SimpleTestCase):
    def assert_bounce(self, **kwargs):
        result = validate_mail(make_parsed(**kwargs))
        self.assertFalse(result.is_valid)
        self.assertEqual(result.error_code, MailErrorCode.BOUNCE)
        self.assertTrue(result.is_bounce)

    def test_mailer_daemon_sender_detected(self):
        self.assert_bounce(sender="mailer-daemon@googlemail.com")

    def test_postmaster_sender_detected(self):
        self.assert_bounce(sender="postmaster@example.com")

    def test_delivery_status_subject_detected(self):
        self.assert_bounce(subject="Delivery Status Notification (Failure)")

    def test_undeliverable_subject_detected(self):
        self.assert_bounce(subject="Undeliverable: your message")

    def test_bounce_wins_over_empty_content(self):
        """A bounce with no body is still reported as a bounce."""
        result = validate_mail(make_parsed(subject="Mail delivery failed", body=""))
        self.assertEqual(result.error_code, MailErrorCode.BOUNCE)


class SizeTests(SimpleTestCase):
    def test_oversize_message_rejected(self):
        result = validate_mail(make_parsed(body="x" * 500), max_size_bytes=100)
        self.assertFalse(result.is_valid)
        self.assertEqual(result.error_code, MailErrorCode.MESSAGE_TOO_LARGE)

    def test_zero_limit_means_no_limit(self):
        result = validate_mail(make_parsed(body="x" * 5000), max_size_bytes=0)
        self.assertTrue(result.is_valid)
