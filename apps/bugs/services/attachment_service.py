"""Attachment storage (spec 31)."""

import uuid

from django.core.files.storage import default_storage
from django.db import transaction

from apps.audit.models import AuditAction
from apps.bugs.models import BugAttachment
from common.services.audit import record_audit
from common.utils.dates import local_now
from common.validators.files import sanitize_filename, validate_upload


@transaction.atomic
def store_attachment(*, bug, uploaded_file, actor, context="BUG", bug_update=None,
                     request=None):
    extension, mime, checksum = validate_upload(uploaded_file)

    display_name = sanitize_filename(uploaded_file.name)
    # The stored name shares nothing with what the user supplied, which removes
    # path traversal and collision as categories of problem rather than trying
    # to sanitise our way out of them.
    stored_name = f"{uuid.uuid4().hex}.{extension}"
    now = local_now()
    relative_path = f"bug_attachments/{now:%Y/%m}/{bug.bug_no}/{stored_name}"

    saved_path = default_storage.save(relative_path, uploaded_file)

    attachment = BugAttachment.objects.create(
        bug=bug, bug_update=bug_update, context=context,
        file_name=display_name, stored_file_name=stored_name,
        file_path=saved_path, file_type=mime, file_extension=extension,
        file_size=uploaded_file.size, checksum_sha256=checksum,
        uploaded_by=actor,
    )

    record_audit(action=AuditAction.ATTACHMENT_UPLOAD, entity=bug, actor=actor,
                 new_value=display_name, request=request)
    return attachment


@transaction.atomic
def delete_attachment(*, attachment, actor, request=None):
    """Soft delete. The bytes stay for audit; a separate retention job purges."""
    attachment.is_deleted = True
    attachment.is_active = False
    attachment.deleted_at = local_now()
    attachment.deleted_by = getattr(actor, "unique_id", None)
    attachment.save(update_fields=["is_deleted", "is_active", "deleted_at", "deleted_by"])

    record_audit(action=AuditAction.ATTACHMENT_DELETE, entity=attachment.bug,
                 actor=actor, old_value=attachment.file_name, request=request)
    return attachment
