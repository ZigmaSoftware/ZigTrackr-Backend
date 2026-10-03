"""Team management endpoints (spec 37)."""

from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from apps.bugs.constants import TERMINAL_STATUSES, BugStatus
from apps.bugs.selectors import base_bug_queryset
from apps.bugs.serializers import BugListSerializer
from apps.dashboard.selectors import team_workload
from common.permissions.require import RequirePermission
from common.permissions.scoping import scope_bug_queryset
from common.responses import ok


class DeveloperWorkloadView(APIView):
    permission_classes = [IsAuthenticated, RequirePermission("teams.workload.view")]

    def get(self, request):
        return ok(team_workload(request.user), message="Workload retrieved.")


class AssignmentBoardView(APIView):
    """Unassigned queue plus workload, so a lead can assign with context."""

    permission_classes = [IsAuthenticated, RequirePermission("teams.assignment.use")]

    def get(self, request):
        qs = scope_bug_queryset(base_bug_queryset(), request.user)
        unassigned = qs.filter(owner__isnull=True).exclude(status__in=TERMINAL_STATUSES)
        return ok({
            "unassigned": BugListSerializer(unassigned[:100], many=True).data,
            "workload": team_workload(request.user),
        }, message="Assignment board retrieved.")
