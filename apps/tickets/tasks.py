"""At-least-once outbound mail delivery with durable retries."""

import logging
from datetime import timedelta

from celery import shared_task
from django.conf import settings
from django.core.mail import EmailMultiAlternatives, get_connection
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from apps.audit.models import AuditAction
from apps.tickets.models import OutboundMail, SupportTicket
from common.services.audit import record_audit

logger = logging.getLogger(__name__)
MAX_ATTEMPTS = 5
LEASE = timedelta(minutes=5)


@shared_task(name="apps.tickets.tasks.deliver_outbound_mail", queue="mail-outbound")
def deliver_outbound_mail(job_id):
    now = timezone.now()
    with transaction.atomic():
        job = OutboundMail.objects.select_for_update().get(pk=job_id)
        if job.status in (OutboundMail.Status.SENT, OutboundMail.Status.FAILED):
            return job.status
        if job.status == OutboundMail.Status.SENDING and job.claimed_at and job.claimed_at > now - LEASE:
            return "IN_FLIGHT"
        if job.next_attempt_at and job.next_attempt_at > now:
            return "NOT_DUE"
        job.status = OutboundMail.Status.SENDING
        job.claimed_at = now
        job.attempts += 1
        job.save(update_fields=["status", "claimed_at", "attempts"])

    # Network I/O is deliberately outside the row lock and DB transaction.
    try:
        email = EmailMultiAlternatives(
            subject=job.subject, body=job.body,
            from_email=job.from_email,
            to=[job.recipient],
            connection=get_connection(timeout=settings.EMAIL_TIMEOUT),
        )
        email.extra_headers["Message-ID"] = f"<{job.message_id}>"
        email.extra_headers["Auto-Submitted"] = "auto-replied" if job.kind == "ACK" else "auto-generated"
        if job.in_reply_to:
            email.extra_headers["In-Reply-To"] = f"<{job.in_reply_to}>"
            email.extra_headers["References"] = f"<{job.in_reply_to}>"
        if email.send() != 1:
            raise RuntimeError("SMTP did not accept the message")
    except Exception as exc:
        logger.exception("Outbound mail %s failed (attempt %s)", job.pk, job.attempts)
        with transaction.atomic():
            job = OutboundMail.objects.select_for_update().get(pk=job_id)
            job.last_error = str(exc)[:500]
            job.status = (OutboundMail.Status.FAILED if job.attempts >= MAX_ATTEMPTS
                          else OutboundMail.Status.RETRY)
            job.next_attempt_at = (None if job.status == OutboundMail.Status.FAILED
                                   else timezone.now() + timedelta(seconds=min(2 ** job.attempts * 15, 3600)))
            job.save(update_fields=["last_error", "status", "next_attempt_at"])
        return job.status

    with transaction.atomic():
        job = OutboundMail.objects.select_for_update().get(pk=job_id)
        job.status = OutboundMail.Status.SENT
        job.sent_at = timezone.now()
        job.last_error = ""
        job.next_attempt_at = None
        job.save(update_fields=["status", "sent_at", "last_error", "next_attempt_at"])
        if job.kind == "ACK":
            SupportTicket.objects.filter(pk=job.ticket_id).update(
                ack_sent_at=job.sent_at, ack_message_id=job.message_id,
            )
        record_audit(action=AuditAction.MAIL_ACK_SENT, entity=job.ticket,
                     actor=None, new_value=job.ticket.reference,
                     metadata={"source": "SYSTEM", "message_id": job.message_id,
                               "kind": job.kind})
    return "SENT"


@shared_task(name="apps.tickets.tasks.recover_outbound_mail", queue="mail-outbound")
def recover_outbound_mail():
    now = timezone.now()
    due = OutboundMail.objects.filter(
        Q(status=OutboundMail.Status.PENDING)
        | Q(status=OutboundMail.Status.RETRY, next_attempt_at__lte=now)
        | Q(status=OutboundMail.Status.SENDING, claimed_at__lte=now - LEASE)
    ).order_by("created_at").values_list("pk", flat=True)[:50]
    for job_id in due:
        deliver_outbound_mail.delay(job_id)
