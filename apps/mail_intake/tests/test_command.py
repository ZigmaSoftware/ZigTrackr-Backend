"""The process_support_mail command (spec 41, 42)."""

from io import StringIO
from unittest.mock import MagicMock, patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings

from apps.mail_intake.models import MailIntake
from apps.mail_intake.tests.factories import make_raw_email
from apps.tickets.models import SupportTicket

MAIL_SETTINGS = dict(
    MAIL_INTAKE_ENABLED=True,
    MAIL_INTAKE_IMAP_HOST="imap.example.com",
    MAIL_INTAKE_USERNAME="intake@example.com",
    MAIL_INTAKE_APP_PASSWORD="app-password",
    MAIL_INTAKE_EMAIL="intake@example.com",
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
)


class FakeIMAP:
    """Stands in for imaplib so the command can be driven without a mail server."""

    def __init__(self, messages):
        self._messages = messages
        self.seen = []
        self.fetches = []

    def select(self, folder):
        return "OK", [b""]

    def uid(self, command, *args):
        command = command.upper()
        if command == "SEARCH":
            return "OK", [" ".join(self._messages).encode()]
        if command == "FETCH":
            self.fetches.append(args)
            uid = args[0]
            return "OK", [(b"1 (RFC822 {1})", self._messages[uid])]
        if command == "STORE":
            self.seen.append(args[0])
            return "OK", [b""]
        return "OK", [b""]

    def noop(self):
        return "OK", [b""]

    def close(self):
        pass

    def logout(self):
        pass


def fake_connection(messages):
    """Patch imap_connection to yield a FakeIMAP."""
    import contextlib

    fake = FakeIMAP(messages)

    @contextlib.contextmanager
    def _cm(config):
        yield fake

    return _cm, fake


@override_settings(**MAIL_SETTINGS)
class CommandTests(TestCase):
    def _run(self, messages, **kwargs):
        cm, fake = fake_connection(messages)
        out = StringIO()
        with patch("apps.mail_intake.services.imap_client.imap_connection", cm):
            call_command("process_support_mail", stdout=out, **kwargs)
        return out.getvalue(), fake

    def test_processes_new_mail_into_a_ticket(self):
        messages = {"1": make_raw_email(subject="[BUG] Command test", message_id="<c1@x>")}
        output, fake = self._run(messages)
        self.assertIn("fetched=1", output)
        self.assertEqual(MailIntake.objects.count(), 1)
        self.assertEqual(SupportTicket.objects.count(), 1)
        self.assertEqual(fake.fetches[0][1], "(BODY.PEEK[])")
        self.assertEqual(fake.seen, ["1"])

    def test_empty_mail_is_marked_seen_without_registration(self):
        messages = {
            "1": make_raw_email(
                subject="",
                body="Hi Team,\n\nRegards,\nSupport",
                message_id="<empty@x>",
            )
        }
        output, fake = self._run(messages)
        self.assertIn("empty=1", output)
        self.assertEqual(MailIntake.objects.count(), 0)
        self.assertEqual(SupportTicket.objects.count(), 0)
        self.assertEqual(fake.seen, ["1"])

    def test_dry_run_writes_nothing(self):
        messages = {"1": make_raw_email(subject="[BUG] Dry run", message_id="<d1@x>")}
        output, fake = self._run(messages, dry_run=True)
        self.assertIn("DRY RUN", output)
        self.assertEqual(MailIntake.objects.count(), 0)
        self.assertEqual(SupportTicket.objects.count(), 0)
        self.assertEqual(fake.seen, [])

    def test_limit_is_respected(self):
        messages = {
            str(i): make_raw_email(subject=f"[BUG] Msg {i}", message_id=f"<m{i}@x>")
            for i in range(1, 6)
        }
        self._run(messages, limit=2)
        self.assertEqual(MailIntake.objects.count(), 2)

    def test_running_twice_creates_one_ticket_per_message(self):
        """Idempotent: re-running over the same mailbox must not duplicate."""
        messages = {"1": make_raw_email(subject="[BUG] Repeat", message_id="<r1@x>")}
        self._run(messages)
        cm, _ = fake_connection(messages)
        with patch("apps.mail_intake.services.imap_client.imap_connection", cm):
            call_command("process_support_mail", stdout=StringIO())
        self.assertEqual(MailIntake.objects.count(), 1)
        self.assertEqual(SupportTicket.objects.count(), 1)

    def test_invalid_since_is_rejected(self):
        with self.assertRaises(CommandError):
            call_command("process_support_mail", since="not-a-date", stdout=StringIO())

    def test_second_run_skips_when_lock_is_held(self):
        out = StringIO()
        import contextlib

        @contextlib.contextmanager
        def busy_lock(name, timeout=0):
            yield False

        with patch("apps.mail_intake.management.commands.process_support_mail.advisory_lock", busy_lock):
            call_command("process_support_mail", stdout=out)
        self.assertIn("holds the lock", out.getvalue())
        self.assertEqual(MailIntake.objects.count(), 0)


class DisabledTests(TestCase):
    @override_settings(MAIL_INTAKE_ENABLED=False)
    def test_disabled_exits_cleanly(self):
        out = StringIO()
        call_command("process_support_mail", stdout=out)
        self.assertIn("nothing to do", out.getvalue())
        self.assertEqual(MailIntake.objects.count(), 0)
