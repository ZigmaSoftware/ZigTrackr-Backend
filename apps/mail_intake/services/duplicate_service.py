"""Registering a message exactly once (spec 11, 42).

The strategy is INSERT-first against the UNIQUE index on dedupe_key, catching
IntegrityError. Not select_for_update, and not an advisory lock:

* SELECT ... FOR UPDATE cannot protect a row that does not exist yet. The race
  here is two workers inserting the same NEW message; on a missing row InnoDB
  takes a gap lock, which is a well-known deadlock source under concurrent
  inserts of scattered keys. Wrong tool.
* The unique index is a guard the database already provides, and it is race-free
  by construction: exactly one INSERT wins and the loser re-reads.

Advisory locks do have a place, but a coarse one -- see common/db/locks.py, used
once per run so overlapping cron ticks do not both poll the mailbox.
"""

import logging

from django.db import IntegrityError, transaction

from apps.mail_intake.constants import MailProcessingStatus
from apps.mail_intake.models import MailIntake, MailProcessingHistory

logger = logging.getLogger(__name__)


def find_existing(dedupe_key):
    if not dedupe_key:
        return None
    return MailIntake.objects.filter(dedupe_key=dedupe_key).first()


def register_mail(parsed, *, dedupe_key, normalized_subject="", normalized_body="",
                  body_hash="", mailbox_folder="INBOX", mailbox_address=""):
    """Persist a newly fetched message. Returns (mail, created).

    Never raises IntegrityError to the caller: a losing race is an ordinary
    outcome here, not an error.
    """
    existing = find_existing(dedupe_key)
    if existing is not None:
        return existing, False

    try:
        # The inner atomic() is load-bearing, not decoration. Once MariaDB
        # raises IntegrityError the transaction is marked for rollback, so
        # catching it inside an OUTER atomic block and carrying on raises
        # TransactionManagementError. This block creates the savepoint the
        # handler below can recover past.
        with transaction.atomic():
            mail = MailIntake.objects.create(
                dedupe_key=dedupe_key,
                provider_message_id=parsed.provider_message_id,
                provider_thread_id=parsed.provider_thread_id,
                message_id=parsed.message_id,
                in_reply_to=parsed.in_reply_to,
                references_header=parsed.references_header,
                from_email=parsed.from_email,
                from_name=parsed.from_name,
                to_emails=list(parsed.to_emails),
                cc_emails=list(parsed.cc_emails),
                reply_to_email=parsed.reply_to_email,
                subject=parsed.subject,
                body_text=parsed.body_text,
                body_html=parsed.body_html,
                normalized_subject=normalized_subject,
                normalized_body=normalized_body,
                body_hash=body_hash,
                raw_size_bytes=parsed.raw_size_bytes,
                received_at=parsed.received_at,
                mailbox_folder=mailbox_folder,
                mailbox_address=mailbox_address,
                processing_status=MailProcessingStatus.RECEIVED,
            )
            MailProcessingHistory.objects.create(
                mail=mail,
                from_status="",
                to_status=MailProcessingStatus.RECEIVED,
                action="RECEIVED",
                remarks=f"Fetched from {mailbox_folder}.",
            )
        return mail, True

    except IntegrityError:
        # Another worker inserted the same message between our check and our
        # write. Its row is the canonical one.
        logger.info("Concurrent registration lost for dedupe_key=%s", dedupe_key[:16])
        existing = find_existing(dedupe_key)
        if existing is None:  # pragma: no cover - would mean a different constraint
            raise
        return existing, False


@transaction.atomic
def mark_duplicate(*, mail, original=None, actor=None):
    """Flag a message as a duplicate of one already processed."""
    from apps.mail_intake.services.history_service import transition

    mail.is_duplicate = True
    mail.save(update_fields=["is_duplicate", "updated_at"])

    remarks = "Message already processed."
    if original is not None and original.pk != mail.pk:
        remarks = f"Duplicate of mail #{original.pk}."

    return transition(
        mail=mail,
        to_status=MailProcessingStatus.DUPLICATE,
        action="DUPLICATE_DETECTED",
        remarks=remarks,
        actor=actor,
        processed=True,
    )
