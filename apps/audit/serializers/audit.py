from rest_framework import serializers

from apps.audit.models import AuditLog


class AuditLogSerializer(serializers.ModelSerializer):
    id = serializers.UUIDField(source="unique_id", read_only=True)
    action_label = serializers.CharField(source="get_action_display", read_only=True)

    class Meta:
        model = AuditLog
        fields = [
            "id", "entity_type", "entity_label", "entity_unique_id",
            "action", "action_label", "field_name", "old_value", "new_value",
            "remarks", "performed_by_name", "performed_at", "ip_address",
        ]
