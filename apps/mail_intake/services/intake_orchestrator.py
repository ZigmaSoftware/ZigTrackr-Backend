"""The pipeline assembly (spec 5, 43).

This is the only module that knows the whole story. Everything below it does one
job and is independently testable.

Transaction boundaries are the important part here. Spec 43 forbids holding a
database transaction open across network I/O, so processing one message is four
SHORT transactions with the slow work between them:

    TXN-1  register the message                  -> commit
           store attachments (file I/O)
    TXN-2  validate                              -> commit
           thread lookup (reads only)
    TXN-3  thread update, or classify and create -> commit
           send acknowledgement (SMTP)
    TXN-4  record the acknowledgement            -> commit

A crash between any two leaves the message in a well-defined status that the
retry sweep resumes from -- which is why status is persisted per step rather
than once at the end.
"""

import logging
from dataclasses import dataclass, field

from django.conf import settings
from django.db import transaction

from apps.mail_intake.constants import (
    MailErrorCode,
    MailProcessingStatus,
    RETRYABLE_MAIL_STATUSES,
)
from apps.mail_intake.models import MailIntake
from apps.mail_intake.services import duplicate_service, history_service
from apps.mail_intake.services.attachment_service import store_mail_attachments
from apps.mail_intake.services.context import build_mail_context
from apps.mail_intake.services.error_service import classify_error, should_retry
from apps.mail_intake.services.mail_normalizer import (
    body_fingerprint,
    clean_readable_body,
    compute_dedupe_key,
    has_meaningful_content,
    normalize_subject,
    normalize_text,
)
from apps.mail_intake.services.mail_validator import validate_content, validate_mail
from apps.mail_intake.services.sanitize import sanitize_email_html
from apps.mail_intake.services.thread_service import apply_thread_update, find_thread_ticket

logger = logging.getLogger(__name__)


@dataclass
class IntakeRunResult:
    fetched: int = 0
    registered: int = 0
    duplicates: int = 0
    rejected: int = 0
    thread_updates: int = 0
    tickets_created: int = 0
    failed: int = 0
    empty_skipped: int = 0
    promotional_skipped: int = 0
    no_reply_skipped: int = 0
    mail_ids: list = field(default_factory=list)

    @property
    def skipped(self):
        return self.empty_skipped + self.promotional_skipped + self.no_reply_skipped

    def as_dict(self):
        return {
            "fetched": self.fetched,
            "registered": self.registered,
            "duplicates": self.duplicates,
            "rejected": self.rejected,
            "thread_updates": self.thread_updates,
            "tickets_created": self.tickets_created,
            "failed": self.failed,
            "skipped": self.skipped,
            "empty_skipped": self.empty_skipped,
            "promotional_skipped": self.promotional_skipped,
            "no_reply_skipped": self.no_reply_skipped,
        }


def _max_size_bytes():
    return int(getattr(settings, "MAIL_INTAKE_MAX_SIZE_MB", 25)) * 1024 * 1024


def _record_skipped_message(result, error_code, parsed):
    if error_code == MailErrorCode.EMPTY_CONTENT:
        result.empty_skipped += 1
    elif error_code == MailErrorCode.PROMOTIONAL_MAIL:
        result.promotional_skipped += 1
    elif error_code == MailErrorCode.NO_REPLY:
        result.no_reply_skipped += 1
    logger.info("Skipping %s mail from %s", error_code, parsed.from_email)


def process_parsed_mail(parsed, *, result=None, actor=None, mailbox_folder="INBOX",
                        mailbox_address="", request=None):
    """Run one parsed message through the whole pipeline.

    Returns the MailIntake row, or None when the message is intentionally skipped.
    Exceptions are caught and recorded rather than propagated: one malformed
    message must not abort a run.
    """
    result = result or IntakeRunResult()

    normalized_subject = normalize_subject(parsed.subject)
    cleaned_body = clean_readable_body(parsed.body_text, parsed.body_html)
    normalized_body = normalize_text(cleaned_body)
    preflight = validate_mail(
        parsed,
        allowed_recipients=getattr(settings, "MAIL_INTAKE_ALLOWED_RECIPIENTS", ()),
        max_size_bytes=_max_size_bytes(),
        normalized_body=normalized_body,
        cleaned_body=cleaned_body,
    )
    if preflight.error_code in {
        MailErrorCode.EMPTY_CONTENT,
        MailErrorCode.PROMOTIONAL_MAIL,
        MailErrorCode.NO_REPLY,
    }:
        _record_skipped_message(result, preflight.error_code, parsed)
        return None

    body_hash = body_fingerprint(normalized_body)
    dedupe_key = compute_dedupe_key(
        message_id=parsed.message_id,
        from_email=parsed.from_email,
        received_at=parsed.received_at,
        normalized_subject=normalized_subject,
        body_hash=body_hash,
    )

    # ---- TXN-1: registration ----
    mail, created = duplicate_service.register_mail(
        parsed,
        dedupe_key=dedupe_key,
        normalized_subject=normalized_subject,
        normalized_body=normalized_body,
        body_hash=body_hash,
        mailbox_folder=mailbox_folder,
        mailbox_address=mailbox_address,
    )

    if not created:
        if mail.processing_status not in RETRYABLE_MAIL_STATUSES:
            result.duplicates += 1
            logger.info("Skipping already-processed mail %s", mail.pk)
            return mail
        logger.info("Resuming mail %s from %s", mail.pk, mail.processing_status)
    else:
        result.registered += 1
        result.mail_ids.append(mail.pk)

    return _continue_processing(
        mail, parsed=parsed, result=result, actor=actor, request=request
    )


def _continue_processing(mail, *, parsed=None, result, actor=None, request=None):
    history_service.record_attempt(mail=mail)

    try:
        cleaned_body = clean_readable_body(mail.body_text, mail.body_html)
        if parsed is None and not has_meaningful_content(mail.subject, cleaned_body):
            return _reject(
                mail,
                validate_content(subject=mail.subject, cleaned_body=cleaned_body),
                result=result,
                actor=actor,
            )

        # ---- Attachments: file I/O, outside any transaction ----
        # Before validation so that a rejected attachment is still recorded and
        # visible to a reviewer.
        if parsed is not None and parsed.attachments and not mail.attachments.exists():
            store_mail_attachments(mail=mail, attachments=parsed.attachments)

        # Sanitise once at ingestion; raw body_html is never served.
        if mail.body_html and not mail.body_html_sanitized:
            safe_html, _ = sanitize_email_html(mail.body_html)
            mail.body_html_sanitized = safe_html
            mail.save(update_fields=["body_html_sanitized", "updated_at"])

        # ---- TXN-2: validation ----
        if parsed is not None:
            history_service.transition(
                mail=mail, to_status=MailProcessingStatus.VALIDATING,
                action="VALIDATING", actor=actor,
            )
            verdict = validate_mail(
                parsed,
                allowed_recipients=getattr(settings, "MAIL_INTAKE_ALLOWED_RECIPIENTS", ()),
                max_size_bytes=_max_size_bytes(),
                normalized_body=mail.normalized_body,
                cleaned_body=cleaned_body,
            )
            if not verdict.is_valid:
                return _reject(mail, verdict, result=result, actor=actor)

        # ---- Thread detection: reads only ----
        match = find_thread_ticket(mail)
        if match.matched and match.is_certain:
            apply_thread_update(mail=mail, ticket=match.ticket, actor=actor, request=request)
            result.thread_updates += 1
            return mail

        history_service.transition(
            mail=mail,
            to_status=MailProcessingStatus.READY_FOR_CLASSIFICATION,
            action="READY_FOR_CLASSIFICATION",
            remarks=match.reason,
            actor=actor,
        )

        # ---- Classification + TXN-3 ----
        return _classify_and_create(
            mail, result=result, actor=actor, request=request,
            ambiguous_thread=bool(match.reason and not match.matched),
        )

    except Exception as exc:  # noqa: BLE001 - deliberate: one bad message must not stop the run
        return _fail(mail, exc, result=result, actor=actor)


def _reject(mail, verdict, *, result, actor=None):
    mail.is_auto_reply = verdict.is_auto_reply
    mail.is_bounce = verdict.is_bounce
    mail.save(update_fields=["is_auto_reply", "is_bounce", "updated_at"])

    if verdict.error_code == MailErrorCode.DUPLICATE:
        duplicate_service.mark_duplicate(mail=mail, actor=actor)
        result.duplicates += 1
        return mail

    history_service.transition(
        mail=mail,
        to_status=MailProcessingStatus.REJECTED,
        action="REJECTED",
        remarks=verdict.reason,
        error_code=verdict.error_code,
        actor=actor,
        processed=True,
    )
    result.rejected += 1
    return mail


def _classify_and_create(mail, *, result, actor=None, request=None, ambiguous_thread=False):
    from apps.classification.services.classification_service import classify_mail
    from apps.tickets.services.outbound_mail import queue_acknowledgement
    from apps.tickets.services.ticket_service import create_ticket_from_mail

    classification = classify_mail(build_mail_context(mail))

    # An explicit marker is the sender's deliberate request for a known
    # workflow. Mapping fields may be unavailable at intake time, but that is
    # handled by assignment/review instead of burying the request.
    from apps.tickets.services.ticket_service import explicit_subject_ticket_type, is_bug_subject
    explicit_type = explicit_subject_ticket_type(mail.subject)
    if explicit_type and not ambiguous_thread:
        classification = type(classification)(
            **{
                **classification.__dict__,
                "ticket_type": explicit_type,
                "needs_review": False,
                "review_reason": f"Explicit {explicit_type} marker.",
            }
        )
    elif not is_bug_subject(mail.subject):
        # General mail must be quarantined for an operator rather than becoming
        # an unexpected visible ticket (job alerts and unrelated messages are
        # common in shared inboxes).
        classification = type(classification)(
            **{
                **classification.__dict__,
                "needs_review": True,
                "review_reason": "Missing required [BUG] subject marker.",
            }
        )

    if ambiguous_thread:
        # An uncertain thread match is itself a reason for a human to look.
        classification = type(classification)(
            **{
                **classification.__dict__,
                "needs_review": True,
                "review_reason": (
                    f"{classification.review_reason} Thread linkage was uncertain."
                ).strip(),
            }
        )

    history_service.transition(
        mail=mail,
        to_status=MailProcessingStatus.CLASSIFIED,
        action="CLASSIFIED",
        remarks=(
            f"{classification.ticket_type} "
            f"({classification.classification_score}) {classification.review_reason}"
        ).strip(),
        actor=actor,
    )

    with transaction.atomic():
        ticket = create_ticket_from_mail(
            mail=mail, classification=classification, actor=actor, request=request
        )
        _record_classification_audit(mail=mail, ticket=ticket, classification=classification)

        history_service.transition(
            mail=mail,
            to_status=(
                MailProcessingStatus.NEEDS_REVIEW
                if classification.needs_review
                else MailProcessingStatus.TICKET_CREATED
            ),
            action="TICKET_CREATED",
            remarks=f"Created {ticket.reference}.",
            actor=actor,
            processed=not classification.needs_review,
        )
        queue_acknowledgement(ticket=ticket, to_email=mail.from_email,
                              in_reply_to=mail.message_id)

    result.tickets_created += 1
    return mail


def _record_classification_audit(*, mail, ticket, classification):
    """Capture prediction and outcome from day one (spec 33)."""
    from apps.classification.models import ClassificationAudit
    from apps.masters.models import ModuleMaster, ProjectMaster

    def resolve(model, unique_id):
        if not unique_id:
            return None
        return model.objects.filter(unique_id=unique_id).first()

    ClassificationAudit.objects.create(
        mail=mail,
        ticket=ticket,
        original_subject=(mail.subject or "")[:255],
        original_body=(mail.body_text or "")[:8000],
        rule_predicted_type=classification.ticket_type,
        rule_score=classification.classification_score,
        matched_rules=[m.as_dict() for m in classification.matched_rules],
        all_scores=dict(classification.all_scores),
        rule_project=resolve(ProjectMaster, classification.project_unique_id),
        rule_module=resolve(ModuleMaster, classification.module_unique_id),
        review_reason=(classification.review_reason or "")[:255],
    )


def _fail(mail, exc, *, result, actor=None):
    error_code, _ = classify_error(exc)
    max_attempts = int(getattr(settings, "MAIL_INTAKE_MAX_ATTEMPTS", 3))
    retryable = should_retry(
        error_code=error_code,
        attempts=mail.processing_attempts or 0,
        max_attempts=max_attempts,
    )

    logger.exception("Processing failed for mail %s (%s)", mail.pk, error_code)
    history_service.transition(
        mail=mail,
        to_status=MailProcessingStatus.PROCESSING_FAILED,
        action="PROCESSING_FAILED",
        # The message, never a stack trace: spec 44 forbids exposing those.
        remarks=str(exc)[:2000],
        error_code=error_code,
        actor=actor,
        processed=not retryable,
    )
    result.failed += 1
    return mail


def run_intake(*, limit=None, since=None, dry_run=False, unseen_only=True,
               actor=None, folder=None):
    """Fetch and process new mail. Returns an IntakeRunResult.

    The IMAP connection stays open across the per-message work, but no database
    transaction is ever open across an IMAP or SMTP call -- that is what spec 43
    actually asks for.
    """
    from apps.mail_intake.services import imap_client
    from apps.mail_intake.services.mail_parser import parse_raw_email

    result = IntakeRunResult()
    limit = limit or int(getattr(settings, "MAIL_INTAKE_BATCH_LIMIT", 50))

    # Resume anything an earlier run left mid-pipeline before pulling new mail.
    retry_pending(limit=limit, actor=actor, result=result)

    config = imap_client.load_mailbox_config()
    mailbox_folder = folder or config.folder

    with imap_client.imap_connection(config) as connection:
        if folder:
            connection.select(folder)

        uids = imap_client.search_uids(connection, unseen_only=unseen_only, since=since)
        result.fetched = len(uids)

        for uid in uids[:limit]:
            try:
                raw = imap_client.fetch_raw(connection, uid)
                parsed = parse_raw_email(raw, provider_message_id=uid)
            except Exception:
                logger.exception("Could not fetch or parse uid %s", uid)
                result.failed += 1
                continue

            if dry_run:
                logger.info(
                    "DRY RUN uid=%s from=%s subject=%r",
                    uid, parsed.from_email, parsed.subject[:80],
                )
                continue

            mail = process_parsed_mail(
                parsed,
                result=result,
                actor=actor,
                mailbox_folder=mailbox_folder,
                mailbox_address=config.address,
            )

            if mail is None or mail.pk:
                imap_client.mark_seen(connection, uid)

    return result


def retry_pending(*, limit=50, actor=None, result=None):
    """Re-drive messages left mid-pipeline by an earlier run.

    Needs no mailbox connection: the content is already persisted, which is the
    payoff for splitting processing across several short transactions.
    """
    result = result or IntakeRunResult()
    max_attempts = int(getattr(settings, "MAIL_INTAKE_MAX_ATTEMPTS", 3))

    pending = (
        MailIntake.objects.filter(
            processing_status__in=RETRYABLE_MAIL_STATUSES,
            processing_attempts__lt=max_attempts,
        )
        .order_by("received_at")[:limit]
    )

    for mail in pending:
        _continue_processing(mail, parsed=None, result=result, actor=actor)
    return result
