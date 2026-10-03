"""Storing mail attachments safely (spec 24, 45).

No validation logic is written here. common/validators/files.py already does
magic-byte sniffing, extension allow-listing, size capping and SHA-256 for bug
attachments, and an email attachment is exactly the same problem. The only work
this module does is bridge raw bytes to the interface that validator expects.

Runs OUTSIDE the registration transaction on purpose: writing files inside a
transaction produces orphaned files on rollback. Orphan files are sweepable;
orphan rows pointing at missing files are not.
"""

import logging
import uuid

from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework import serializers as drf_serializers

from apps.mail_intake.models import MailAttachment
from common.validators.files import sanitize_filename, validate_upload

logger = logging.getLogger(__name__)


def _first_error(exc):
    """Flatten a DRF ValidationError into one readable sentence."""
    detail = getattr(exc, "detail", None)
    if isinstance(detail, dict):
        for messages in detail.values():
            if isinstance(messages, (list, tuple)) and messages:
                return str(messages[0])
            return str(messages)
    if isinstance(detail, (list, tuple)) and detail:
        return str(detail[0])
    return str(detail or exc)


def store_mail_attachments(*, mail, attachments):
    """Validate and persist each attachment, recording rejections.

    A rejected attachment still gets a row with is_rejected=True and no bytes on
    disk: dropping it silently leaves a reviewer wondering why the message
    mentions a screenshot that is not there.
    """
    stored = []

    for attachment in attachments or ():
        display_name = sanitize_filename(attachment.file_name)

        # SimpleUploadedFile satisfies the whole UploadedFile interface the
        # validator uses -- .size, .name, .read, .seek, .chunks.
        candidate = SimpleUploadedFile(
            name=display_name,
            content=attachment.content,
            content_type=attachment.content_type or "application/octet-stream",
        )

        try:
            extension, mime, checksum = validate_upload(candidate)
        except drf_serializers.ValidationError as exc:
            reason = _first_error(exc)
            logger.info(
                "Rejected attachment %r on mail %s: %s", display_name, mail.pk, reason
            )
            stored.append(
                MailAttachment.objects.create(
                    mail=mail,
                    file_name=display_name,
                    file_size=len(attachment.content or b""),
                    content_id=attachment.content_id,
                    is_inline=attachment.is_inline,
                    is_rejected=True,
                    rejection_reason=reason[:255],
                )
            )
            continue

        stored_name = f"{uuid.uuid4().hex}.{extension}"
        # Path keyed on unique_id rather than the sequential pk, so the
        # directory is not guessable. MEDIA_ROOT is not served statically
        # anyway; downloads go through a permission-gated view.
        path = (
            f"mail_attachments/{mail.received_at:%Y/%m}/{mail.unique_id}/{stored_name}"
        )

        try:
            saved_path = default_storage.save(path, ContentFile(attachment.content))
        except OSError:
            logger.exception("Failed to store attachment %r for mail %s", display_name, mail.pk)
            raise

        stored.append(
            MailAttachment.objects.create(
                mail=mail,
                file_name=display_name,
                stored_file_name=stored_name,
                file_path=saved_path,
                file_type=mime,
                file_extension=extension,
                file_size=len(attachment.content or b""),
                checksum_sha256=checksum,
                content_id=attachment.content_id,
                is_inline=attachment.is_inline,
            )
        )

    return stored
