from rest_framework import serializers

from apps.notifications.models import Notification


class NotificationSerializer(serializers.ModelSerializer):
    id = serializers.UUIDField(source="unique_id", read_only=True)
    type_label = serializers.CharField(source="get_notification_type_display", read_only=True)
    bug_no = serializers.CharField(source="bug.bug_no", read_only=True, default=None)
    bug_id = serializers.UUIDField(source="bug.unique_id", read_only=True, default=None)
    ticket_no = serializers.CharField(source="ticket.reference", read_only=True, default=None)
    ticket_id = serializers.UUIDField(source="ticket.unique_id", read_only=True, default=None)
    # Lets the client choose a persistent toast over an auto-dismissing one
    # without hardcoding the type list in two places.
    is_urgent = serializers.SerializerMethodField()

    class Meta:
        model = Notification
        fields = ["id", "notification_type", "type_label", "title", "message",
                  "bug_no", "bug_id", "ticket_no", "ticket_id", "is_urgent",
                  "link", "is_read", "read_at", "created_at"]

    def get_is_urgent(self, obj):
        from apps.notifications.models.notification import URGENT_TYPES

        return obj.notification_type in URGENT_TYPES
