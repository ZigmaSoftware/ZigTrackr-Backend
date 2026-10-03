"""Persist requester notices with their ticket change; SMTP runs in Celery."""

import logging
from email.utils import make_msgid

from django.conf import settings
from django.db import transaction

from apps.mail_intake.services.mail_parser import strip_angle_brackets
from apps.tickets.models import OutboundMail
from apps.tickets.services.ack_service import (
    ACK_BODY, ASSIGNED_BODY, CLOSED_BODY, build_ack_subject, should_acknowledge,
)

logger = logging.getLogger(__name__)


def _enqueue(*, ticket, kind, event_key, recipient, subject, body, in_reply_to=""):
    if not settings.TICKET_ACK_ENABLED or not should_acknowledge(recipient):
        return None
    from_email = settings.DEFAULT_FROM_EMAIL or "no-reply@localhost"
    message_id = strip_angle_brackets(make_msgid(domain=from_email.split("@")[-1]))
    job, created = OutboundMail.objects.get_or_create(
        event_key=event_key,
        defaults={
            "ticket": ticket, "kind": kind, "from_email": from_email,
            "recipient": recipient,
            "subject": subject[:255], "body": body, "message_id": message_id,
            "in_reply_to": strip_angle_brackets(in_reply_to) if in_reply_to else "",
        },
    )
    if created:
        # Publishing is only a fast path. The periodic sweep recovers jobs when
        # Redis is down; broker failure must never roll back a saved ticket.
        def publish():
            try:
                from apps.tickets.tasks import deliver_outbound_mail
                deliver_outbound_mail.apply_async(args=[job.pk], retry=False)
            except Exception:
                logger.exception("Outbound mail %s saved but could not be published", job.pk)

        transaction.on_commit(publish)
    return job


def queue_acknowledgement(*, ticket, to_email, in_reply_to=""):
    job = _enqueue(
        ticket=ticket, kind="ACK", event_key=f"ACK:{ticket.pk}",
        recipient=to_email,
        subject=build_ack_subject(ticket),
        body=ACK_BODY.format(ticket_no=ticket.reference, title=ticket.title,
                             status=ticket.get_status_display()),
        in_reply_to=in_reply_to,
    )
    if job and ticket.ack_message_id != job.message_id:
        # Persist before SMTP so even a crash after send can link a reply.
        ticket.ack_message_id = job.message_id
        ticket.save(update_fields=["ack_message_id", "updated_at"])
    return job


def queue_assigned_notice(*, ticket):
    if not ticket.ticket_no:
        return None
    base = (settings.PUBLIC_APP_URL or "").rstrip("/")
    return _enqueue(
        ticket=ticket, kind="ASSIGNED", event_key=f"ASSIGNED:{ticket.pk}",
        recipient=(ticket.reported_by_email or "").strip(),
        subject=build_ack_subject(ticket),
        body=ASSIGNED_BODY.format(
            ticket_no=ticket.ticket_no, title=ticket.title,
            track_url=f"{base}/track" if base else "your support portal",
        ),
        in_reply_to=ticket.ack_message_id,
    )


def queue_closed_notice(*, ticket, remarks=""):
    base = (settings.PUBLIC_APP_URL or "").rstrip("/")
    remarks_block = f"\n  Remarks : {remarks.strip()}\n" if remarks.strip() else ""
    return _enqueue(
        ticket=ticket, kind="CLOSED",
        event_key=f"CLOSED:{ticket.pk}:{ticket.updated_at.isoformat()}",
        recipient=(ticket.reported_by_email or "").strip(),
        subject=build_ack_subject(ticket),
        body=CLOSED_BODY.format(
            ticket_no=ticket.reference, title=ticket.title,
            remarks_block=remarks_block,
            track_url=f"{base}/track" if base else "your support portal",
        ),
        in_reply_to=ticket.ack_message_id,
    )
