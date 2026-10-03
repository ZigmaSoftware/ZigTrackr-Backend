"""The single choke point for mail status changes (spec 25).

Every transition goes through transition(), so mail_intake.processing_status and
the immutable history log can never disagree. The pattern is borrowed from
apps/bugs/services/update_service.py, where the denormalised current state is
refreshed in the same transaction as the history row for the same reason.
"""

from django.db import transaction
from django.utils import timezone

from apps.mail_intake.models import MailProcessingHistory


@transaction.atomic
def transition(*, mail, to_status, action, remarks="", error_code="", actor=None,
               processed=False):
    """Move `mail` to `to_status` and append the matching history row."""
    from_status = mail.processing_status

    history = MailProcessingHistory.objects.create(
        mail=mail,
        from_status=from_status or "",
        to_status=to_status,
        action=action,
        remarks=remarks[:2000] if remarks else "",
        error_code=error_code or "",
        performed_by=actor if getattr(actor, "pk", None) else None,
    )

    mail.processing_status = to_status
    update_fields = ["processing_status", "updated_at"]

    if error_code:
        mail.last_error_code = error_code
        mail.last_error_message = (remarks or "")[:2000]
        update_fields += ["last_error_code", "last_error_message"]

    if processed:
        mail.processed_at = timezone.now()
        update_fields.append("processed_at")

    mail.save(update_fields=update_fields)
    return history


@transaction.atomic
def record_attempt(*, mail):
    """Count one processing pass over this message."""
    mail.processing_attempts = (mail.processing_attempts or 0) + 1
    mail.last_attempt_at = timezone.now()
    mail.save(update_fields=["processing_attempts", "last_attempt_at", "updated_at"])
    return mail.processing_attempts
