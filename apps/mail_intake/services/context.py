"""Building a MailContext from a stored message.

Lives here, on the intake side, rather than as a constructor on the DTO. If
classification imported mail_intake to build its own input, the one-way
dependency that spec 6 requires would be broken, and a Phase-2 provider swap
would stop being a settings change.
"""

from apps.classification.dto import MailContext
from apps.mail_intake.services.mail_normalizer import clean_readable_body


def build_mail_context(mail):
    return MailContext(
        subject=mail.subject or "",
        body_text=clean_readable_body(mail.body_text, mail.body_html),
        normalized_subject=mail.normalized_subject or "",
        normalized_body=mail.normalized_body or "",
        from_email=mail.from_email or "",
        to_emails=tuple(mail.to_emails or ()),
        has_attachments=mail.attachments.exists() if mail.pk else False,
        mail_unique_id=str(mail.unique_id),
    )
