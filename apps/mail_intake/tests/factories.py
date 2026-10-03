"""Test helpers for mail intake.

Plain functions, matching apps/bugs/tests/factories.py.

make_raw_email() builds genuine RFC822 bytes with email.message.EmailMessage
rather than hand-written strings: the parser's job is to cope with real
encodings, so the tests should feed it real ones.
"""

from email.message import EmailMessage

from django.utils import timezone

from apps.mail_intake.services.mail_normalizer import compute_dedupe_key, normalize_body
from apps.mail_intake.services.mail_parser import parse_raw_email

DEFAULT_SENDER = "sadham@zigmaglobal.in"
DEFAULT_MAILBOX = "bperp23@gmail.com"


def make_raw_email(
    *, subject="Test subject", body="Test body", sender=DEFAULT_SENDER,
    to=DEFAULT_MAILBOX, cc=None, message_id="<msg-1@example.com>",
    in_reply_to=None, references=None, date="Thu, 18 Sep 2026 10:30:00 +0530",
    html_body=None, attachments=(), extra_headers=None, content_type=None,
):
    """Build raw RFC822 bytes for a message."""
    message = EmailMessage()
    if sender:
        message["From"] = sender
    if to:
        message["To"] = to
    if cc:
        message["Cc"] = cc
    if subject is not None:
        message["Subject"] = subject
    if message_id:
        message["Message-ID"] = message_id
    if in_reply_to:
        message["In-Reply-To"] = in_reply_to
    if references:
        message["References"] = references
    if date:
        message["Date"] = date
    for key, value in (extra_headers or {}).items():
        message[key] = value

    message.set_content(body or "")
    if html_body:
        message.add_alternative(html_body, subtype="html")

    for name, payload, maintype, subtype in attachments:
        message.add_attachment(
            payload, maintype=maintype, subtype=subtype, filename=name
        )

    if content_type:
        message.replace_header("Content-Type", content_type)

    return bytes(message)


def make_parsed(**kwargs):
    """Parse a message built from the same keyword arguments."""
    return parse_raw_email(make_raw_email(**kwargs))


def make_mail_intake(**overrides):
    """Persist a MailIntake row with sensible defaults."""
    from apps.mail_intake.models import MailIntake

    subject = overrides.pop("subject", "Test subject")
    body = overrides.pop("body_text", "Test body")
    normalized = normalize_body(body)
    fields = {
        "from_email": DEFAULT_SENDER,
        "to_emails": [DEFAULT_MAILBOX],
        "subject": subject,
        "body_text": body,
        "normalized_body": normalized,
        "received_at": timezone.now(),
        "mailbox_folder": "INBOX",
    }
    fields.update(overrides)
    fields.setdefault(
        "dedupe_key",
        compute_dedupe_key(
            message_id=fields.get("message_id", "") or f"auto-{subject}",
        ),
    )
    return MailIntake.objects.create(**fields)
