"""Ticket attachment storage.

Mirrors apps/bugs/services/attachment_service.py -- see that module for why the
stored name shares nothing with the uploaded one, and why deletion is soft.
"""

import uuid

from django.core.files.storage import default_storage
from django.db import transaction

from apps.audit.models import AuditAction
from apps.tickets.models import TicketAttachment
from common.services.audit import record_audit
from common.utils.dates import local_now
from common.validators.files import sanitize_filename, validate_upload


@transaction.atomic
def store_ticket_attachment(*, ticket, uploaded_file, actor, reason="", request=None):
    extension, mime, checksum = validate_upload(uploaded_file)

    display_name = sanitize_filename(uploaded_file.name)
    stored_name = f"{uuid.uuid4().hex}.{extension}"
    now = local_now()
    # Grouped by the intake reference, which every ticket has from creation --
    # ticket_no is null until the ticket is routed.
    relative_path = f"ticket_attachments/{now:%Y/%m}/{ticket.ref_no}/{stored_name}"

    saved_path = default_storage.save(relative_path, uploaded_file)

    attachment = TicketAttachment.objects.create(
        ticket=ticket,
        reason=(reason or "").strip()[:255],
        file_name=display_name, stored_file_name=stored_name,
        file_path=saved_path, file_type=mime, file_extension=extension,
        file_size=uploaded_file.size, checksum_sha256=checksum,
        uploaded_by=actor,
    )

    record_audit(action=AuditAction.ATTACHMENT_UPLOAD, entity=ticket, actor=actor,
                 new_value=display_name, remarks=attachment.reason, request=request)
    from apps.tickets.services.activity_service import record_activity

    record_activity(
        ticket=ticket, event_type="ATTACHMENT_ADDED", title="Attachment added",
        description=f"{actor.display_name} attached {display_name}. {attachment.reason}",
        actor=actor,
    )
    return attachment


@transaction.atomic
def delete_ticket_attachment(*, attachment, actor, request=None):
    """Soft delete. The bytes stay for audit; a separate retention job purges."""
    attachment.is_deleted = True
    attachment.is_active = False
    attachment.deleted_at = local_now()
    attachment.deleted_by = getattr(actor, "unique_id", None)
    attachment.save(update_fields=["is_deleted", "is_active", "deleted_at", "deleted_by"])

    record_audit(action=AuditAction.ATTACHMENT_DELETE, entity=attachment.ticket,
                 actor=actor, old_value=attachment.file_name, request=request)
    return attachment
