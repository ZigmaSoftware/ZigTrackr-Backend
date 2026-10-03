"""Dashboard endpoints (spec 21)."""

from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from apps.bugs.constants import DAILY_UPDATE_REQUIRED_STATUSES, TERMINAL_STATUSES, BugStatus
from apps.bugs.selectors import base_bug_queryset
from apps.bugs.serializers import BugListSerializer
from apps.dashboard.selectors import (
    average_metrics,
    bugs_by_aging_band,
    bugs_by_module,
    bugs_by_priority,
    bugs_by_severity,
    bugs_by_status,
    closure_trend,
    dashboard_kpis,
    root_cause_distribution,
    sidebar_counts,
    team_workload,
)
from common.permissions.require import RequirePermission
from common.permissions.scoping import scope_bug_queryset
from common.responses import ok


class KpiView(APIView):
    permission_classes = [IsAuthenticated, RequirePermission("dashboard.dashboard.view")]

    def get(self, request):
        data = dashboard_kpis(request.user)
        data.update(average_metrics(request.user))
        return ok(data, message="Dashboard metrics retrieved.")


class ChartsView(APIView):
    permission_classes = [IsAuthenticated, RequirePermission("dashboard.dashboard.view")]

    def get(self, request):
        user = request.user
        return ok({
            "by_status": bugs_by_status(user),
            "by_priority": bugs_by_priority(user),
            "by_severity": bugs_by_severity(user),
            "by_module": bugs_by_module(user),
            "by_aging": bugs_by_aging_band(user),
            "root_cause": root_cause_distribution(user),
            "closure_trend": closure_trend(user),
        }, message="Chart data retrieved.")


class MyWorkView(APIView):
    """Spec 21.3 "My Work": what the signed-in user owes today."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user
        qs = scope_bug_queryset(base_bug_queryset(), user)
        mine = qs.filter(owner=user)

        def serialize(queryset, limit=10):
            return BugListSerializer(queryset[:limit], many=True).data

        return ok({
            "assigned_to_me": serialize(mine.filter(status=BugStatus.ASSIGNED)),
            "in_progress": serialize(mine.filter(status=BugStatus.IN_PROGRESS)),
            "testing": serialize(mine.filter(status=BugStatus.TESTING)),
            "update_pending": serialize(
                mine.filter(is_update_pending=True).order_by("-days_since_update")),
        }, message="My work retrieved.")


class PriorityAttentionView(APIView):
    """Spec 21.3 "Priority Attention": what the team must look at now."""

    permission_classes = [IsAuthenticated, RequirePermission("dashboard.dashboard.view")]

    def get(self, request):
        qs = scope_bug_queryset(base_bug_queryset(), request.user)

        def serialize(queryset, limit=10):
            return BugListSerializer(queryset[:limit], many=True).data

        # Both CLOSED and REJECTED are terminal (spec 29): a rejected bug is
        # done, not merely unassigned or no-longer-critical, so it must not
        # reappear in the "needs attention" panel. Excluding only CLOSED left
        # every rejected bug showing up under Critical/Unassigned forever.
        return ok({
            "critical": serialize(
                qs.filter(priority__code="CRITICAL").exclude(status__in=TERMINAL_STATUSES)),
            "overdue": serialize(qs.filter(is_overdue=True).order_by("-overdue_days")),
            "update_pending": serialize(
                qs.filter(is_update_pending=True).order_by("-days_since_update")),
            "unassigned": serialize(
                qs.filter(owner__isnull=True).exclude(status__in=TERMINAL_STATUSES)),
        }, message="Priority attention retrieved.")


class SidebarCountsView(APIView):
    """Spec 17.6: live badge counts."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        return ok(sidebar_counts(request.user), message="Counts retrieved.")


class TeamOverviewView(APIView):
    permission_classes = [IsAuthenticated, RequirePermission("teams.workload.view")]

    def get(self, request):
        return ok(team_workload(request.user), message="Team workload retrieved.")


class RecentActivityView(APIView):
    """Spec 21.3 Recent Activity feed."""

    permission_classes = [IsAuthenticated, RequirePermission("dashboard.dashboard.view")]

    def get(self, request):
        from apps.audit.models import AuditLog

        visible_bug_ids = scope_bug_queryset(
            base_bug_queryset(), request.user
        ).values_list("id", flat=True)

        rows = (AuditLog.objects
                .filter(entity_type="Bug", entity_id__in=visible_bug_ids)
                .select_related("performed_by")
                .order_by("-performed_at")[:25])

        return ok([{
            "id": str(row.unique_id),
            "action": row.action,
            "action_label": row.get_action_display(),
            "bug_no": row.entity_label,
            "bug_id": str(row.entity_unique_id) if row.entity_unique_id else None,
            "actor": row.performed_by_name,
            "field": row.field_name,
            "old_value": row.old_value,
            "new_value": row.new_value,
            "timestamp": row.performed_at,
        } for row in rows], message="Recent activity retrieved.")
