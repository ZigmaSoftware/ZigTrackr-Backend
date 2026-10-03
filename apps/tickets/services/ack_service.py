"""Outbound acknowledgement mail.

The acknowledgement is not a courtesy: its subject carries [TKT-YYMM-NNNN] and
its Message-ID is what the user's reply points at, so this is what makes spec
12's reply threading work at all.

Two rules govern the implementation:

  1. Never inside a database transaction. SMTP is network I/O (spec 43), and a
     send that succeeded followed by a rollback would acknowledge a ticket that
     does not exist.
  2. Never raises. A mail server problem must not undo a ticket that was
     created successfully.
"""

import logging
from email.utils import make_msgid

from django.conf import settings
from django.core.mail import EmailMultiAlternatives, get_connection
from django.db import transaction
from django.utils import timezone

from apps.audit.models import AuditAction
from apps.mail_intake.constants import NO_ACK_LOCAL_PARTS
from apps.mail_intake.services.mail_parser import strip_angle_brackets
from common.services.audit import record_audit

logger = logging.getLogger(__name__)

ACK_BODY = """Thank you for contacting support.

Your request has been logged as {ticket_no}.

  Subject : {title}
  Status  : {status}

Please keep {ticket_no} in the subject line when replying, so your message is
added to the same ticket.

This is an automated acknowledgement; please do not reply with unrelated
requests.
"""


ASSIGNED_BODY = """Your request has been accepted and is now being worked on.

  Ticket  : {ticket_no}
  Subject : {title}

Track its progress here:

  {track_url}

Enter ticket number {ticket_no} and this email address to open your request.
You can view updates and message our support team in the Chat tab there.
"""

CLOSED_BODY = """Your request has been completed and closed.

  Ticket  : {ticket_no}
  Subject : {title}
{remarks_block}
If the issue is not resolved, you can reopen this ticket on the tracking page
within 2 days (48 hours) of its closure:

  {track_url}

Enter ticket number {ticket_no} and this email address to view the ticket.
After 48 hours, please submit a new request if you still need help.
"""


def _send(*, ticket, to_email, subject, body, request=None, audit_action=None):
    """Send one plain notification about a ticket. Never raises.

    Threading headers point at our acknowledgement so the message lands in the
    conversation the requester already has, rather than starting a new one.
    """
    if not getattr(settings, "TICKET_ACK_ENABLED", True):
        return False
    if not should_acknowledge(to_email):
        logger.info("Skipping notification for %s (loop-guard)", (to_email or "")[:80])
        return False

    from_email = getattr(settings, "DEFAULT_FROM_EMAIL", "") or "no-reply@localhost"
    try:
        email = EmailMultiAlternatives(
            subject=subject, body=body, from_email=from_email, to=[to_email],
            connection=get_connection(timeout=getattr(settings, "EMAIL_TIMEOUT", 10)),
        )
        if ticket.ack_message_id:
            bracketed = f"<{strip_angle_brackets(ticket.ack_message_id)}>"
            email.extra_headers["In-Reply-To"] = bracketed
            email.extra_headers["References"] = bracketed
        email.extra_headers["Auto-Submitted"] = "auto-generated"
        email.send()
    except Exception:
        logger.exception("Notification send failed for %s; the ticket stands", ticket.reference)
        return False

    if audit_action is not None:
        record_audit(action=audit_action, entity=ticket, actor=None,
                     new_value=ticket.reference, request=request)
    return True


def send_ticket_assigned_notice(*, ticket, request=None):
    """Tell the requester their ticket now has a number, and how to track it.

    Sent when the TL assigns and the TKT number is minted -- before that the
    request has only an intake reference and nothing to track.
    """
    to_email = (ticket.reported_by_email or "").strip()
    if not to_email or not ticket.ticket_no:
        return False

    base = (getattr(settings, "PUBLIC_APP_URL", "") or "").rstrip("/")
    return _send(
        ticket=ticket,
        to_email=to_email,
        subject=build_ack_subject(ticket),
        body=ASSIGNED_BODY.format(
            ticket_no=ticket.ticket_no,
            title=ticket.title,
            track_url=f"{base}/track" if base else "your support portal",
        ),
        request=request,
        audit_action=AuditAction.MAIL_ACK_SENT,
    )


def send_ticket_closed_notice(*, ticket, remarks="", request=None):
    """Tell the requester the work is done. Sent when a tester closes."""
    to_email = (ticket.reported_by_email or "").strip()
    if not to_email:
        return False

    remarks_block = f"\n  Remarks : {remarks.strip()}\n" if remarks.strip() else ""
    base = (getattr(settings, "PUBLIC_APP_URL", "") or "").rstrip("/")
    return _send(
        ticket=ticket,
        to_email=to_email,
        subject=build_ack_subject(ticket),
        body=CLOSED_BODY.format(
            ticket_no=ticket.reference, title=ticket.title, remarks_block=remarks_block,
            track_url=f"{base}/track" if base else "your support portal",
        ),
        request=request,
        audit_action=AuditAction.MAIL_ACK_SENT,
    )


def build_ack_subject(ticket):
    """[REF-2609-0001] Original subject -- the format spec 12 asks users to reply to.

    Quotes ticket.reference, not ticket_no: acknowledgement is sent at intake,
    before the ticket has been routed and given a TKT number, so the intake ref
    is the only identifier that exists yet. Inbound matching accepts both.
    """
    return f"[{ticket.reference}] {ticket.title}".strip()


def should_acknowledge(to_email):
    """Whether it is safe to send an acknowledgement to this address.

    Refusing daemon and no-reply addresses is mail-loop prevention, not
    politeness: two systems acknowledging each other fill a mailbox in minutes.
    """
    address = (to_email or "").strip().lower()
    if not address or "@" not in address:
        return False

    intake = (getattr(settings, "MAIL_INTAKE_EMAIL", "") or "").strip().lower()
    if intake and address == intake:
        return False

    sender = (getattr(settings, "DEFAULT_FROM_EMAIL", "") or "").strip().lower()
    if sender and address == sender:
        return False

    local_part = address.split("@", 1)[0]
    return not any(local_part.startswith(prefix) for prefix in NO_ACK_LOCAL_PARTS)


def send_ticket_acknowledgement(*, ticket, to_email, in_reply_to="", request=None):
    """Send the acknowledgement. Returns its Message-ID, or "" if not sent.

    Never raises.
    """
    if not getattr(settings, "TICKET_ACK_ENABLED", True):
        return ""

    if not should_acknowledge(to_email):
        logger.info(
            "Skipping acknowledgement for %s (loop-guard)", (to_email or "")[:80]
        )
        return ""

    from_email = getattr(settings, "DEFAULT_FROM_EMAIL", "") or "no-reply@localhost"
    domain = from_email.split("@")[-1] or "localhost"

    # Generated up front rather than left to Django. EmailMessage.message() only
    # invents a Message-ID if extra_headers lacks one, so setting it explicitly
    # both wins and -- more importantly -- means the value is known before the
    # send, and survives a failure part way through it.
    message_id = make_msgid(domain=domain)

    try:
        email = EmailMultiAlternatives(
            subject=build_ack_subject(ticket),
            body=ACK_BODY.format(
                ticket_no=ticket.reference,
                title=ticket.title,
                status=ticket.get_status_display(),
            ),
            from_email=from_email,
            to=[to_email],
            connection=get_connection(
                timeout=getattr(settings, "EMAIL_TIMEOUT", 10)
            ),
        )
        email.extra_headers["Message-ID"] = message_id
        if in_reply_to:
            bracketed = f"<{strip_angle_brackets(in_reply_to)}>"
            email.extra_headers["In-Reply-To"] = bracketed
            email.extra_headers["References"] = bracketed
        # So that if this acknowledgement itself hits an autoresponder, our own
        # auto-reply detection catches the return trip.
        email.extra_headers["Auto-Submitted"] = "auto-replied"

        # fail_silently is deliberately NOT used: it swallows the error without
        # a log line, which is how a silently broken mail server goes unnoticed.
        email.send()

    except Exception:
        logger.exception(
            "Acknowledgement send failed for %s; the ticket stands", ticket.reference
        )
        return ""

    bare_id = strip_angle_brackets(message_id)
    _record_ack(ticket=ticket, message_id=bare_id, request=request)
    return bare_id


@transaction.atomic
def _record_ack(*, ticket, message_id, request=None):
    """Persist the sent acknowledgement's id, stored without angle brackets.

    Bracket handling must match MailIntake.message_id exactly: thread matching
    compares the two columns directly, and an inconsistency there is the classic
    silent threading failure.
    """
    ticket.ack_sent_at = timezone.now()
    ticket.ack_message_id = message_id
    ticket.save(update_fields=["ack_sent_at", "ack_message_id", "updated_at"])

    record_audit(
        action=AuditAction.MAIL_ACK_SENT,
        entity=ticket,
        actor=None,
        new_value=ticket.reference,
        metadata={"source": "SYSTEM", "message_id": message_id},
        request=request,
    )
    return ticket
