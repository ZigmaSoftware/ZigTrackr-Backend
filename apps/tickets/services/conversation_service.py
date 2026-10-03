"""Safe, readable requester mail thread for ticket details."""

from apps.mail_intake.models import MailIntake
from apps.mail_intake.services.mail_normalizer import clean_readable_body


def request_messages(ticket):
    mails = list(
        MailIntake.objects.filter(linked_ticket=ticket)
        .prefetch_related("attachments").order_by("is_thread_reply", "received_at", "id")
    )
    if not mails:
        return [{
            "id": str(ticket.unique_id),
            "kind": "ORIGINAL",
            "from_email": ticket.reported_by_email,
            "from_name": ticket.reported_by_name,
            "subject": ticket.title,
            "received_at": ticket.created_at,
            "body": ticket.description,
            "attachment_count": 0,
        }]
    return [{
        "id": str(mail.unique_id),
        "kind": "REPLY" if mail.is_thread_reply else "ORIGINAL",
        "from_email": mail.from_email,
        "from_name": mail.from_name,
        "subject": mail.subject,
        "received_at": mail.received_at,
        "body": clean_readable_body(mail.body_text, mail.body_html),
        "attachment_count": sum(not row.is_rejected for row in mail.attachments.all()),
    } for mail in mails]
