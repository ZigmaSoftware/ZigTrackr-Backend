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
from apps.tickets.services import mail_templates

logger = logging.getLogger(__name__)


def _enqueue(*, ticket, kind, event_key, recipient, subject, body, html_body="",
             in_reply_to="", expires_at=None):
    # TICKET_ACK_ENABLED governs courtesy notices. An access code is not a
    # courtesy -- gating it would make the public tracking page silently
    # unusable wherever that flag is off -- so OTP is exempt.
    if kind != "OTP" and not settings.TICKET_ACK_ENABLED:
        return None
    if not should_acknowledge(recipient):
        return None
    from_email = settings.DEFAULT_FROM_EMAIL or "no-reply@localhost"
    message_id = strip_angle_brackets(make_msgid(domain=from_email.split("@")[-1]))
    job, created = OutboundMail.objects.get_or_create(
        event_key=event_key,
        defaults={
            "ticket": ticket, "kind": kind, "from_email": from_email,
            "recipient": recipient,
            "subject": subject[:255], "body": body, "html_body": html_body,
            "message_id": message_id,
            "in_reply_to": strip_angle_brackets(in_reply_to) if in_reply_to else "",
            # The periodic sweep retries for up to an hour. A passcode that
            # outlives its own validity must never be delivered, so OTP jobs
            # carry the code's expiry and the task drops them past it.
            "expires_at": expires_at,
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
        html_body=mail_templates.ack_email(
            ticket_no=ticket.reference, title=ticket.title,
            status=ticket.get_status_display(),
        ),
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
        html_body=mail_templates.assigned_email(
            ticket_no=ticket.ticket_no, title=ticket.title,
            track_url=f"{base}/track" if base else "",
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
        html_body=mail_templates.closed_email(
            ticket_no=ticket.reference, title=ticket.title,
            remarks=remarks.strip(),
            track_url=f"{base}/track" if base else "",
        ),
        in_reply_to=ticket.ack_message_id,
    )


def queue_deleted_notice(*, ticket, reason):
    """Snapshot the reason before soft deletion; delivery may happen later."""
    original = ticket.original_mail
    recipient = (ticket.reported_by_email or (original.from_email if original else "") or "").strip()
    # Email titles originate outside the application; never allow header lines.
    title = " ".join(ticket.title.split())
    return _enqueue(
        ticket=ticket, kind="DELETED", event_key=f"DELETED:{ticket.pk}",
        recipient=recipient,
        subject=f"Request deleted [{ticket.reference}] - {title}",
        body=(
            "Hello,\n\n"
            "Your request has been removed from the support queue before assignment.\n\n"
            f"Reference: {ticket.reference}\nRequest: {title}\n\n"
            f"Reason for deletion:\n{reason.strip()}\n\n"
            "If you still need assistance, please send a new request to our support team.\n\n"
            "Regards,\nZigTrackr Support\n"
        ),
        html_body=mail_templates.deleted_email(
            ticket_no=ticket.reference, title=title, reason=reason.strip(),
        ),
        in_reply_to=ticket.ack_message_id or (original.message_id if original else ""),
    )


def queue_access_otp(*, ticket, to_email, code, otp):
    """Email a public-tracking access code.

    event_key carries the OTP row id: a resend must never collide with an
    earlier key, since get_or_create would then silently skip sending the new
    code while the user waits for mail that never arrives.
    """
    # Imported here, not at module scope: ticket_otp_service imports this
    # module to queue the mail, so a top-level import would be circular.
    from apps.tickets.services.ticket_otp_service import OTP_TTL_MINUTES

    text, html = mail_templates.otp_email(code=code, minutes=OTP_TTL_MINUTES)
    return _enqueue(
        ticket=ticket, kind="OTP", event_key=f"OTP:{ticket.pk}:{otp.pk}",
        recipient=to_email,
        # The code is NOT in the subject. Partly because it no longer belongs
        # to one ticket, and partly because a bare number in a subject line is
        # a well-known spam signal -- it reads like the throwaway codes bulk
        # senders use, and filters score it accordingly.
        subject="Your Zigma Support sign-in code",
        body=text, html_body=html,
        expires_at=otp.expires_at,
    )
