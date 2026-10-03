"""Master CRUD endpoints."""

from django.db.models import Count, Q

from apps.masters.models import (
    DepartmentMaster,
    ModuleMaster,
    PriorityMaster,
    ProjectMaster,
    RootCauseTypeMaster,
    SeverityMaster,
    SiteMaster,
    SubmoduleMaster,
    TeamMaster,
)
from apps.masters.serializers import (
    DepartmentSerializer,
    ModuleSerializer,
    PrioritySerializer,
    ProjectSerializer,
    RootCauseTypeSerializer,
    SeveritySerializer,
    SiteSerializer,
    SubmoduleSerializer,
    TeamSerializer,
)
from common.viewsets import (
    AuditedSoftDeleteViewSet,
    PermissionByActionMixin,
    master_permissions,
)


class BaseMasterViewSet(PermissionByActionMixin, AuditedSoftDeleteViewSet):
    permission_map = master_permissions()
    search_fields = ["name", "code"]
    ordering_fields = ["name", "code", "created_at"]


class PriorityViewSet(BaseMasterViewSet):
    queryset = PriorityMaster.objects.all()
    serializer_class = PrioritySerializer
    ordering_fields = ["rank", "name", "code"]
    ordering = ["rank"]
    envelope_messages = {
        "create": "Priority created successfully.",
        "update": "Priority updated successfully.",
        "partial_update": "Priority updated successfully.",
        "destroy": "Priority deactivated successfully.",
    }


class SeverityViewSet(BaseMasterViewSet):
    queryset = SeverityMaster.objects.all()
    serializer_class = SeveritySerializer
    ordering_fields = ["rank", "name", "code"]
    ordering = ["rank"]
    envelope_messages = {
        "create": "Severity created successfully.",
        "update": "Severity updated successfully.",
        "partial_update": "Severity updated successfully.",
        "destroy": "Severity deactivated successfully.",
    }


class RootCauseTypeViewSet(BaseMasterViewSet):
    queryset = RootCauseTypeMaster.objects.all()
    serializer_class = RootCauseTypeSerializer
    ordering_fields = ["rank", "name", "code"]
    ordering = ["rank"]
    envelope_messages = {
        "create": "Root cause type created successfully.",
        "update": "Root cause type updated successfully.",
        "destroy": "Root cause type deactivated successfully.",
    }


class DepartmentViewSet(BaseMasterViewSet):
    queryset = DepartmentMaster.objects.all()
    serializer_class = DepartmentSerializer
    envelope_messages = {
        "create": "Department created successfully.",
        "update": "Department updated successfully.",
        "destroy": "Department deactivated successfully.",
    }


class SiteViewSet(BaseMasterViewSet):
    queryset = SiteMaster.objects.all()
    serializer_class = SiteSerializer
    envelope_messages = {
        "create": "Site created successfully.",
        "update": "Site updated successfully.",
        "destroy": "Site deactivated successfully.",
    }


class TeamViewSet(BaseMasterViewSet):
    queryset = TeamMaster.objects.select_related("team_lead")
    serializer_class = TeamSerializer
    envelope_messages = {
        "create": "Team created successfully.",
        "update": "Team updated successfully.",
        "destroy": "Team deactivated successfully.",
    }


class ProjectViewSet(BaseMasterViewSet):
    serializer_class = ProjectSerializer
    envelope_messages = {
        "create": "Project created successfully.",
        "update": "Project updated successfully.",
        "destroy": "Project deactivated successfully.",
    }

    def get_queryset(self):
        return (
            ProjectMaster.objects.filter(is_deleted=False)
            .select_related("project_lead")
            .annotate(module_count=Count("modules", filter=Q(modules__is_deleted=False)))
        )


class ModuleViewSet(BaseMasterViewSet):
    serializer_class = ModuleSerializer
    filterset_fields = {"project__unique_id": ["exact"]}
    envelope_messages = {
        "create": "Module created successfully.",
        "update": "Module updated successfully.",
        "destroy": "Module deactivated successfully.",
    }

    def get_queryset(self):
        qs = ModuleMaster.objects.filter(is_deleted=False).select_related("project")
        project = self.request.query_params.get("project")
        if project:
            qs = qs.filter(project__unique_id=project)
        if not self._include_inactive():
            qs = qs.filter(is_active=True)
        return qs


class SubmoduleViewSet(BaseMasterViewSet):
    serializer_class = SubmoduleSerializer
    envelope_messages = {
        "create": "Submodule created successfully.",
        "update": "Submodule updated successfully.",
        "destroy": "Submodule deactivated successfully.",
    }

    def get_queryset(self):
        qs = (SubmoduleMaster.objects.filter(is_deleted=False)
              .select_related("module", "module__project"))
        module = self.request.query_params.get("module")
        project = self.request.query_params.get("project")
        if module:
            qs = qs.filter(module__unique_id=module)
        if project:
            qs = qs.filter(module__project__unique_id=project)
        if not self._include_inactive():
            qs = qs.filter(is_active=True)
        return qs
