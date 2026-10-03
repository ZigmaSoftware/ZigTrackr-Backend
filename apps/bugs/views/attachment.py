"""Attachment endpoints, including the gated download (spec 31)."""

from urllib.parse import quote

from django.core.files.storage import default_storage
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from apps.audit.models import AuditAction
from apps.bugs.models import Bug, BugAttachment
from apps.bugs.serializers import BugAttachmentSerializer
from apps.bugs.services.attachment_service import delete_attachment, store_attachment
from common.permissions.require import RequirePermission
from common.permissions.scoping import can_mutate_bug, can_view_bug
from common.responses import created, ok
from common.services.audit import record_audit
from common.validators.files import INLINE_SAFE_TYPES


class BugAttachmentView(APIView):
    """List and upload attachments for one bug."""

    permission_classes = [IsAuthenticated, RequirePermission(
        "bugs.attachment.view", "bugs.attachment.add")]

    def _get_bug(self, request, unique_id):
        bug = get_object_or_404(Bug, unique_id=unique_id, is_deleted=False)
        # 404 rather than 403: do not reveal that a bug exists to someone who
        # cannot see it.
        if not can_view_bug(request.user, bug):
            raise Http404
        return bug

    def get(self, request, unique_id):
        bug = self._get_bug(request, unique_id)
        rows = (BugAttachment.objects
                .filter(bug=bug, is_deleted=False)
                .select_related("uploaded_by"))
        return ok(BugAttachmentSerializer(rows, many=True).data,
                  message="Attachments retrieved.")

    def post(self, request, unique_id):
        bug = self._get_bug(request, unique_id)
        if not can_mutate_bug(request.user, bug):
            from rest_framework.exceptions import PermissionDenied

            raise PermissionDenied("You do not have permission to modify this bug.")

        uploaded = request.FILES.get("file")
        if uploaded is None:
            from rest_framework.exceptions import ValidationError

            raise ValidationError({"file": ["No file was submitted."]})

        attachment = store_attachment(
            bug=bug, uploaded_file=uploaded, actor=request.user,
            context=request.data.get("context", "BUG"), request=request,
        )
        return created(BugAttachmentSerializer(attachment).data,
                       message="Attachment uploaded successfully.")


class AttachmentDownloadView(APIView):
    """Serve an attachment through Django so permissions actually apply.

    MEDIA_ROOT is never exposed by the web server: a static path would hand the
    file to anyone holding the URL, regardless of role or bug visibility.
    """

    permission_classes = [IsAuthenticated, RequirePermission("bugs.attachment.view")]

    def get(self, request, unique_id):
        attachment = get_object_or_404(
            BugAttachment.objects.select_related("bug"),
            unique_id=unique_id, is_deleted=False,
        )
        if not can_view_bug(request.user, attachment.bug):
            raise Http404

        if not default_storage.exists(attachment.file_path):
            raise Http404

        record_audit(action=AuditAction.ATTACHMENT_DOWNLOAD, entity=attachment.bug,
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


class AttachmentDeleteView(APIView):
    permission_classes = [IsAuthenticated, RequirePermission("bugs.attachment.delete")]

    def delete(self, request, unique_id):
        attachment = get_object_or_404(
            BugAttachment.objects.select_related("bug"),
            unique_id=unique_id, is_deleted=False,
        )
        # 404 rather than 403 if the bug is not even visible -- do not confirm
        # the attachment exists to someone who cannot see its bug.
        if not can_view_bug(request.user, attachment.bug):
            raise Http404
        # Deleting is a mutation, not a view: can_view_bug alone let a Team
        # Lead delete attachments on any unassigned bug company-wide, because
        # their *visibility* scope includes every unowned bug (spec 37's
        # triage inbox). can_mutate_bug applies the narrower rule that
        # actually governs whether they may act on this particular bug.
        if not can_mutate_bug(request.user, attachment.bug):
            from rest_framework.exceptions import PermissionDenied

            raise PermissionDenied("You do not have permission to modify this bug.")
        delete_attachment(attachment=attachment, actor=request.user, request=request)
        return ok(None, message="Attachment deleted successfully.")
