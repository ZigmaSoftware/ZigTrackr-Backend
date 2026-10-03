"""In-app notifications (spec 52)."""

from django.utils import timezone
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated

from apps.notifications.models import Notification
from apps.notifications.serializers import NotificationSerializer
from common.responses import ok


class NotificationViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = NotificationSerializer
    lookup_field = "unique_id"
    lookup_url_kwarg = "unique_id"
    permission_classes = [IsAuthenticated]
    ordering = ["-created_at"]

    def get_queryset(self):
        """A user only ever sees their own notifications."""
        qs = Notification.objects.filter(recipient=self.request.user).select_related("bug")
        if self.request.query_params.get("unread", "").lower() in ("1", "true"):
            qs = qs.filter(is_read=False)
        return qs

    @action(detail=False, methods=["get"])
    def unread_count(self, request):
        count = Notification.objects.filter(recipient=request.user, is_read=False).count()
        return ok({"count": count}, message="Unread count retrieved.")

    @action(detail=True, methods=["post"])
    def read(self, request, unique_id=None):
        notification = self.get_object()
        if not notification.is_read:
            notification.is_read = True
            notification.read_at = timezone.now()
            notification.save(update_fields=["is_read", "read_at"])
        return ok(NotificationSerializer(notification).data, message="Marked as read.")

    @action(detail=False, methods=["post"], url_path="read-all")
    def read_all(self, request):
        updated = Notification.objects.filter(
            recipient=request.user, is_read=False
        ).update(is_read=True, read_at=timezone.now())
        return ok({"updated": updated}, message="All notifications marked as read.")
