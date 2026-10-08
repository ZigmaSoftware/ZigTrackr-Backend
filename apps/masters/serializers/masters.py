"""Master serializers."""

from rest_framework import serializers

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

AUDIT_READ_ONLY = ["id", "created_at", "updated_at", "is_deleted"]


class UniqueNameMixin:
    """Case-insensitive name uniqueness among live rows.

    Done in the serializer rather than as a conditional UniqueConstraint
    because MariaDB has no usable partial unique index (spec 2): a constraint
    would also collide with soft-deleted rows, permanently blocking reuse of a
    deleted name.
    """

    scope_field = None

    def validate_name(self, value):
        return value.strip()

    def validate(self, attrs):
        attrs = super().validate(attrs)
        value = attrs.get("name", getattr(self.instance, "name", None))
        if value is None:
            return attrs
        model = self.Meta.model
        qs = model.objects.filter(name__iexact=value, is_deleted=False)
        if self.scope_field:
            # Resolve relations before scoping names. Malformed UUIDs should
            # return a field error, and PATCH can retain the existing parent.
            parent = attrs.get(self.scope_field, getattr(self.instance, self.scope_field, None))
            if parent is None:
                return attrs
            qs = qs.filter(**{self.scope_field: parent})
        if self.instance is not None:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise serializers.ValidationError(
                {"name": [f"A {model._meta.verbose_name} with this name already exists."]}
            )
        return attrs


class BaseMasterSerializer(UniqueNameMixin, serializers.ModelSerializer):
    id = serializers.UUIDField(source="unique_id", read_only=True)


class CodedMasterSerializer(BaseMasterSerializer):
    """Shared shape for priority/severity/root-cause-type.

    `code` is immutable on system rows: application logic filters on it, so
    letting an admin edit the code of a seeded row would silently break the
    dashboard's Critical count.
    """

    def validate_code(self, value):
        if self.instance and self.instance.is_system and value != self.instance.code:
            raise serializers.ValidationError(
                "The code of a system row cannot be changed."
            )
        return value.strip().upper()


class PrioritySerializer(CodedMasterSerializer):
    class Meta:
        model = PriorityMaster
        fields = ["id", "code", "name", "description", "rank", "color",
                  "sla_days", "is_system", "is_active", *AUDIT_READ_ONLY[1:]]
        read_only_fields = [*AUDIT_READ_ONLY, "is_system"]


class SeveritySerializer(CodedMasterSerializer):
    class Meta:
        model = SeverityMaster
        fields = ["id", "code", "name", "description", "rank", "color",
                  "is_system", "is_active", *AUDIT_READ_ONLY[1:]]
        read_only_fields = [*AUDIT_READ_ONLY, "is_system"]


class RootCauseTypeSerializer(CodedMasterSerializer):
    class Meta:
        model = RootCauseTypeMaster
        fields = ["id", "code", "name", "description", "rank", "color",
                  "is_system", "is_active", *AUDIT_READ_ONLY[1:]]
        read_only_fields = [*AUDIT_READ_ONLY, "is_system"]


class DepartmentSerializer(BaseMasterSerializer):
    class Meta:
        model = DepartmentMaster
        fields = ["id", "code", "name", "description", "is_active", *AUDIT_READ_ONLY[1:]]
        read_only_fields = AUDIT_READ_ONLY


class SiteSerializer(BaseMasterSerializer):
    class Meta:
        model = SiteMaster
        fields = ["id", "code", "name", "description", "address", "is_active",
                  *AUDIT_READ_ONLY[1:]]
        read_only_fields = AUDIT_READ_ONLY


class TeamSerializer(BaseMasterSerializer):
    team_lead_name = serializers.CharField(source="team_lead.display_name",
                                           read_only=True, default=None)

    class Meta:
        model = TeamMaster
        fields = ["id", "code", "name", "description", "team_lead", "team_lead_name",
                  "is_active", *AUDIT_READ_ONLY[1:]]
        read_only_fields = AUDIT_READ_ONLY


class ProjectSerializer(BaseMasterSerializer):
    project_lead_name = serializers.CharField(source="project_lead.display_name",
                                              read_only=True, default=None)
    module_count = serializers.IntegerField(read_only=True, default=0)

    class Meta:
        model = ProjectMaster
        fields = ["id", "code", "name", "description", "project_lead",
                  "project_lead_name", "start_date", "module_count", "is_active",
                  *AUDIT_READ_ONLY[1:]]
        read_only_fields = AUDIT_READ_ONLY


class ModuleSerializer(BaseMasterSerializer):
    scope_field = "project"
    project = serializers.SlugRelatedField(
        slug_field="unique_id", write_only=True,
        queryset=ProjectMaster.objects.filter(is_deleted=False),
    )
    project_id = serializers.UUIDField(source="project.unique_id", read_only=True)
    project_name = serializers.CharField(source="project.name", read_only=True)

    class Meta:
        model = ModuleMaster
        fields = ["id", "code", "name", "description", "project", "project_id",
                  "project_name", "is_active", *AUDIT_READ_ONLY[1:]]
        read_only_fields = AUDIT_READ_ONLY
        extra_kwargs = {"project": {"write_only": True}}


class SubmoduleSerializer(BaseMasterSerializer):
    scope_field = "module"
    module = serializers.SlugRelatedField(
        slug_field="unique_id", write_only=True,
        queryset=ModuleMaster.objects.filter(is_deleted=False),
    )
    module_id = serializers.UUIDField(source="module.unique_id", read_only=True)
    module_name = serializers.CharField(source="module.name", read_only=True)
    project_name = serializers.CharField(source="module.project.name", read_only=True)

    class Meta:
        model = SubmoduleMaster
        fields = ["id", "code", "name", "description", "module", "module_id",
                  "module_name", "project_name", "is_active", *AUDIT_READ_ONLY[1:]]
        read_only_fields = AUDIT_READ_ONLY
        extra_kwargs = {"module": {"write_only": True}}
