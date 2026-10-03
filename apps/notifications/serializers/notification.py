from rest_framework import serializers

from apps.notifications.models import Notification


class NotificationSerializer(serializers.ModelSerializer):
    id = serializers.UUIDField(source="unique_id", read_only=True)
    type_label = serializers.CharField(source="get_notification_type_display", read_only=True)
    bug_no = serializers.CharField(source="bug.bug_no", read_only=True, default=None)
    bug_id = serializers.UUIDField(source="bug.unique_id", read_only=True, default=None)

    class Meta:
        model = Notification
        fields = ["id", "notification_type", "type_label", "title", "message",
                  "bug_no", "bug_id", "link", "is_read", "read_at", "created_at"]
