"""One definition of visible requester-mail files for counts and listings."""

from apps.mail_intake.models import MailAttachment


def valid_email_attachments():
    return MailAttachment.objects.filter(is_rejected=False).exclude(file_path="")
