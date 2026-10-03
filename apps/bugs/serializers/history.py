"""Serializers for the immutable history tables."""

from rest_framework import serializers

from apps.accounts.serializers import UserLiteSerializer
from apps.bugs.models import (
    BugAssignmentHistory,
    BugAttachment,
    BugReopenHistory,
    BugStatusHistory,
    BugTestingHistory,
    BugUpdate,
)


class BugUpdateSerializer(serializers.ModelSerializer):
    id = serializers.UUIDField(source="unique_id", read_only=True)
    updated_by = UserLiteSerializer(read_only=True)
    owner = UserLiteSerializer(read_only=True)
    status_label = serializers.CharField(source="get_status_display", read_only=True)

    class Meta:
        model = BugUpdate
        fields = [
            "id", "status", "status_label", "owner", "update_text", "remarks",
            "next_action", "expected_completion_date", "updated_by",
            "update_date", "created_at", "is_system_generated",
        ]


class BugUpdateWriteSerializer(serializers.Serializer):
    update_text = serializers.CharField()
    remarks = serializers.CharField(required=False, allow_blank=True, default="")
    next_action = serializers.CharField(required=False, allow_blank=True,
                                        max_length=255, default="")
    expected_completion_date = serializers.DateField(required=False, allow_null=True)


class BugStatusHistorySerializer(serializers.ModelSerializer):
    id = serializers.UUIDField(source="unique_id", read_only=True)
    changed_by = UserLiteSerializer(read_only=True)
    from_status_label = serializers.SerializerMethodField()
    to_status_label = serializers.CharField(source="get_to_status_display", read_only=True)

    class Meta:
        model = BugStatusHistory
        fields = ["id", "from_status", "from_status_label", "to_status",
                  "to_status_label", "remarks", "changed_by", "changed_at"]

    def get_from_status_label(self, obj):
        from apps.bugs.constants import BugStatus

        return BugStatus(obj.from_status).label if obj.from_status else ""


class BugAssignmentHistorySerializer(serializers.ModelSerializer):
    id = serializers.UUIDField(source="unique_id", read_only=True)
    from_owner = UserLiteSerializer(read_only=True)
    to_owner = UserLiteSerializer(read_only=True)
    assigned_by = UserLiteSerializer(read_only=True)

    class Meta:
        model = BugAssignmentHistory
        fields = ["id", "from_owner", "to_owner", "assigned_by", "assigned_at", "remarks"]


class BugTestingHistorySerializer(serializers.ModelSerializer):
    id = serializers.UUIDField(source="unique_id", read_only=True)
    tested_by = UserLiteSerializer(read_only=True)
    test_result_label = serializers.CharField(source="get_test_result_display", read_only=True)

    class Meta:
        model = BugTestingHistory
        fields = ["id", "tested_by", "test_result", "test_result_label",
                  "test_remarks", "tested_at"]


class BugReopenHistorySerializer(serializers.ModelSerializer):
    id = serializers.UUIDField(source="unique_id", read_only=True)
    reopened_by = UserLiteSerializer(read_only=True)

    class Meta:
        model = BugReopenHistory
        fields = ["id", "reopen_reason", "reopened_by", "reopened_at"]


class BugAttachmentSerializer(serializers.ModelSerializer):
    id = serializers.UUIDField(source="unique_id", read_only=True)
    uploaded_by = UserLiteSerializer(read_only=True)
    download_url = serializers.SerializerMethodField()

    class Meta:
        model = BugAttachment
        fields = ["id", "file_name", "file_type", "file_extension", "file_size",
                  "context", "uploaded_by", "uploaded_at", "download_url"]

    def get_download_url(self, obj):
        # Always the gated view, never a direct MEDIA_ROOT path (spec 31).
        return f"/api/v1/bugs/attachments/{obj.unique_id}/download/"
