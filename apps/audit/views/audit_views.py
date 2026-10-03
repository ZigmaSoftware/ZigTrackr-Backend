"""Audit log endpoints (spec 41)."""

from django.db.models import Q
from rest_framework import viewsets
from rest_framework.permissions import IsAuthenticated

from apps.audit.models import AuditLog
from apps.audit.serializers import AuditLogSerializer
from common.permissions.require import RequirePermission, has_permission
from common.permissions.scoping import scope_bug_queryset


class AuditLogViewSet(viewsets.ReadOnlyModelViewSet):
    """Read-only by design: an audit trail that can be edited is not one."""

    serializer_class = AuditLogSerializer
    lookup_field = "unique_id"
    lookup_url_kwarg = "unique_id"
    permission_classes = [IsAuthenticated, RequirePermission("admin.audit.view")]
    filterset_fields = {"entity_type": ["exact"], "action": ["exact"]}
    search_fields = ["entity_label", "performed_by_name", "field_name"]
    ordering_fields = ["performed_at"]
    ordering = ["-performed_at"]

    def get_queryset(self):
        qs = AuditLog.objects.select_related("performed_by")

        # admin.audit.view is Admin-only today, so this is not currently
        # reachable by anyone with limited bug visibility -- but spec 16's
        # Permission Management screen makes every grant editable at runtime,
        # and an unscoped audit query is a much larger disclosure than an
        # unscoped bug list the day that codename is handed to a non-Admin
        # role. Bug-entity rows are intersected with the caller's normal bug
        # visibility as defense in depth; every other entity type (users,
        # roles, permissions, logins) is Admin-domain data this codename
        # already gates correctly on its own.
        user = self.request.user
        if not (getattr(user, "is_superuser", False) or has_permission(user, "bugs.bug.view_all")):
            from apps.bugs.models import Bug

            visible_bug_ids = scope_bug_queryset(Bug.objects.all(), user).values_list(
                "unique_id", flat=True,
            )
            qs = qs.filter(~Q(entity_type="Bug") | Q(entity_unique_id__in=visible_bug_ids))

        bug = self.request.query_params.get("bug")
        if bug:
            qs = qs.filter(entity_unique_id=bug)
        user = self.request.query_params.get("user")
        if user:
            qs = qs.filter(performed_by__unique_id=user)
        date_from = self.request.query_params.get("date_from")
        if date_from:
            qs = qs.filter(performed_at__date__gte=date_from)
        date_to = self.request.query_params.get("date_to")
        if date_to:
            qs = qs.filter(performed_at__date__lte=date_to)
        return qs
