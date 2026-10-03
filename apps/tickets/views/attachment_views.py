"""Ticket attachment endpoints, including the gated download (spec 31).

Mirrors apps/bugs/views/attachment.py. The permission codenames are the bug
ones on purpose: uploading evidence is one capability, and every role that may
attach a file to a bug may attach one to a ticket.
"""

from urllib.parse import quote

from django.core.files.storage import default_storage
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from apps.audit.models import AuditAction
from apps.mail_intake.models import MailAttachment
from apps.tickets.models import SupportTicket, TicketAttachment
from apps.tickets.serializers import TicketAttachmentSerializer
from apps.tickets.services.attachment_service import (
    delete_ticket_attachment,
    store_ticket_attachment,
)
from common.permissions.require import RequirePermission
from common.permissions.scoping import can_mutate_ticket, can_view_ticket
from common.responses import created, ok
from common.services.audit import record_audit
from common.validators.files import INLINE_SAFE_TYPES


class TicketAttachmentView(APIView):
    """List and upload attachments for one ticket."""

    permission_classes = [IsAuthenticated, RequirePermission(
        "bugs.attachment.view", "bugs.attachment.add")]

    def _get_ticket(self, request, unique_id):
        ticket = get_object_or_404(SupportTicket, unique_id=unique_id, is_deleted=False)
        # 404 rather than 403: do not reveal that a ticket exists to someone who
        # cannot see it.
        if not can_view_ticket(request.user, ticket):
            raise Http404
        return ticket

    def get(self, request, unique_id):
        ticket = self._get_ticket(request, unique_id)
        source = (request.query_params.get("source") or "").upper()
        if source not in {"", "MAIL", "MANUAL"}:
            raise ValidationError({"source": ["Use MAIL or MANUAL."]})

        # The default remains the merged list for existing internal consumers.
        # The requester-mail tab passes source=MAIL so internal uploads cannot
        # accidentally appear alongside files received from email.
        if source == "MAIL":
            manual = []
        else:
            rows = (TicketAttachment.objects
                    .filter(ticket=ticket, is_deleted=False)
                    .select_related("uploaded_by"))
            manual = [
                {**row, "source": "MANUAL", "uploaded_by_name": row["uploaded_by"]["name"]}
                for row in TicketAttachmentSerializer(rows, many=True).data
            ]
        mail_rows = [] if source == "MANUAL" else MailAttachment.objects.filter(
            mail__linked_ticket=ticket, is_rejected=False,
        ).select_related("mail").order_by("mail__received_at", "id")
        incoming = [{
            "id": str(row.unique_id),
            "file_name": row.file_name,
            "file_type": row.file_type,
            "file_extension": row.file_extension,
            "file_size": row.file_size,
            "reason": "Email reply" if row.mail.is_thread_reply else "Original email",
            "uploaded_by": None,
            "uploaded_by_name": row.mail.from_name or row.mail.from_email,
            "uploaded_at": row.mail.received_at,
            "download_url": f"/api/v1/tickets/mail-attachments/{row.unique_id}/download/",
            "source": "MAIL",
        } for row in mail_rows]
        merged = incoming if source == "MAIL" else [*manual, *incoming]
        if source != "MAIL":
            merged.sort(key=lambda row: row["uploaded_at"])
        return ok(merged,
                  message="Attachments retrieved.")

    def post(self, request, unique_id):
        ticket = self._get_ticket(request, unique_id)
        if not can_mutate_ticket(request.user, ticket):
            raise PermissionDenied("You do not have permission to modify this ticket.")

        uploaded = request.FILES.get("file")
        if uploaded is None:
            raise ValidationError({"file": ["No file was submitted."]})

        # Required here, unlike bug attachments: a file on a ticket is evidence
        # for a claim, and the uploader says what it shows.
        reason = (request.data.get("reason") or "").strip()
        if not reason:
            raise ValidationError({"reason": ["Say what this file shows."]})

        attachment = store_ticket_attachment(
            ticket=ticket, uploaded_file=uploaded, actor=request.user,
            reason=reason, request=request,
        )
        return created(TicketAttachmentSerializer(attachment).data,
                       message="Attachment uploaded successfully.")


class TicketAttachmentDownloadView(APIView):
    """Serve an attachment through Django so permissions actually apply.

    MEDIA_ROOT is never exposed by the web server: a static path would hand the
    file to anyone holding the URL, regardless of role or ticket visibility.
    """

    permission_classes = [IsAuthenticated, RequirePermission("bugs.attachment.view")]

    def get(self, request, unique_id):
        attachment = get_object_or_404(
            TicketAttachment.objects.select_related("ticket"),
            unique_id=unique_id, is_deleted=False,
        )
        if not can_view_ticket(request.user, attachment.ticket):
            raise Http404

        if not default_storage.exists(attachment.file_path):
            raise Http404

        record_audit(action=AuditAction.ATTACHMENT_DOWNLOAD, entity=attachment.ticket,
                     actor=request.user, new_value=attachment.file_name, request=request)

        response = FileResponse(
            default_storage.open(attachment.file_path, "rb"),
            content_type=attachment.file_type,
        )
        disposition = "inline" if attachment.file_type in INLINE_SAFE_TYPES else "attachment"
        response["Content-Disposition"] = (
            f"{disposition}; filename*=UTF-8''{quote(attachment.file_name)}"
        )
        # Stop a browser from re-interpreting the bytes as something executable.
        response["X-Content-Type-Options"] = "nosniff"
        return response


class TicketAttachmentDeleteView(APIView):
    permission_classes = [IsAuthenticated, RequirePermission("bugs.attachment.delete")]

    def delete(self, request, unique_id):
        attachment = get_object_or_404(
            TicketAttachment.objects.select_related("ticket"),
            unique_id=unique_id, is_deleted=False,
        )
        if not can_view_ticket(request.user, attachment.ticket):
            raise Http404
        # Deleting is a mutation, not a view: visibility alone is wider than the
        # right to act on this particular ticket.
        if not can_mutate_ticket(request.user, attachment.ticket):
            raise PermissionDenied("You do not have permission to modify this ticket.")
        delete_ticket_attachment(attachment=attachment, actor=request.user, request=request)
        return ok(None, message="Attachment deleted successfully.")


class TicketMailAttachmentDownloadView(APIView):
    permission_classes = [IsAuthenticated, RequirePermission("bugs.attachment.view")]

    def get(self, request, unique_id):
        attachment = get_object_or_404(
            MailAttachment.objects.select_related("mail__linked_ticket"),
            unique_id=unique_id, is_rejected=False,
        )
        ticket = attachment.mail.linked_ticket
        if not ticket or ticket.is_deleted or not can_view_ticket(request.user, ticket):
            raise Http404
        if not attachment.file_path or not default_storage.exists(attachment.file_path):
            raise Http404
        record_audit(
            action=AuditAction.ATTACHMENT_DOWNLOAD, entity=ticket,
            actor=request.user, new_value=attachment.file_name, request=request,
        )
        response = FileResponse(
            default_storage.open(attachment.file_path, "rb"),
            content_type=attachment.file_type,
        )
        disposition = "inline" if attachment.file_type in INLINE_SAFE_TYPES else "attachment"
        response["Content-Disposition"] = (
            f"{disposition}; filename*=UTF-8''{quote(attachment.file_name)}"
        )
        response["X-Content-Type-Options"] = "nosniff"
        return response
