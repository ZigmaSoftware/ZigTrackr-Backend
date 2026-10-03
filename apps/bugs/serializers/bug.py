"""Bug serializers.

Three shapes rather than one: the list screen is the hottest query in the
system (spec 23) and must not pay for nested detail it never renders.
"""

from rest_framework import serializers

from apps.accounts.serializers import UserLiteSerializer
from apps.bugs.constants import BugStatus, Environment
from apps.bugs.models import Bug
from apps.masters.models import (
    DepartmentMaster,
    ModuleMaster,
    PriorityMaster,
    ProjectMaster,
    RootCauseTypeMaster,
    SeverityMaster,
    SiteMaster,
    SubmoduleMaster,
)


class CodedRefSerializer(serializers.Serializer):
    """Priority/severity/root-cause reference carrying its display metadata.

    Sending code, name, colour and rank together means the frontend badge is
    driven by data rather than a hardcoded colour switch, so adding a priority
    in Masters needs no frontend change.
    """

    id = serializers.UUIDField(source="unique_id", read_only=True)
    code = serializers.CharField(read_only=True)
    name = serializers.CharField(read_only=True)
    color = serializers.CharField(read_only=True)
    rank = serializers.IntegerField(read_only=True)


class NamedRefSerializer(serializers.Serializer):
    id = serializers.UUIDField(source="unique_id", read_only=True)
    name = serializers.CharField(read_only=True)


class BugListSerializer(serializers.ModelSerializer):
    """Lean shape for the bug list (spec 23 columns)."""

    id = serializers.UUIDField(source="unique_id", read_only=True)
    project = NamedRefSerializer(read_only=True)
    module = NamedRefSerializer(read_only=True)
    priority = CodedRefSerializer(read_only=True)
    severity = CodedRefSerializer(read_only=True)
    owner = UserLiteSerializer(read_only=True)
    reported_by = UserLiteSerializer(read_only=True)
    status_label = serializers.CharField(source="get_status_display", read_only=True)
    can_mutate = serializers.SerializerMethodField()

    def get_can_mutate(self, obj):
        from common.permissions.scoping import can_mutate_bug

        request = self.context.get("request")
        return bool(request and can_mutate_bug(request.user, obj))

    # Computed in SQL by the annotations; never stored (spec 12, 13).
    age_days = serializers.IntegerField(read_only=True)
    aging_band = serializers.CharField(read_only=True)
    is_overdue = serializers.BooleanField(read_only=True)
    overdue_days = serializers.IntegerField(read_only=True)
    is_update_pending = serializers.BooleanField(read_only=True)
    days_since_update = serializers.IntegerField(read_only=True)

    class Meta:
        model = Bug
        fields = [
            "id", "bug_no", "reported_date", "project", "module", "title", "can_mutate",
            "priority", "severity", "owner", "reported_by", "status", "status_label",
            "age_days", "aging_band", "expected_closure_date", "is_overdue",
            "overdue_days", "is_update_pending", "days_since_update",
            "latest_update_at", "latest_remarks", "reopen_count",
        ]


class BugDetailSerializer(BugListSerializer):
    """Full shape for the detail page (spec 27)."""

    submodule = NamedRefSerializer(read_only=True)
    department = NamedRefSerializer(read_only=True)
    site = NamedRefSerializer(read_only=True)
    root_cause_type = CodedRefSerializer(read_only=True)
    assigned_by = UserLiteSerializer(read_only=True)
    resolved_by = UserLiteSerializer(read_only=True)
    tested_by = UserLiteSerializer(read_only=True)
    closed_by = UserLiteSerializer(read_only=True)
    environment_label = serializers.CharField(source="get_environment_display", read_only=True)
    allowed_transitions = serializers.SerializerMethodField()
    source_mail_id = serializers.SerializerMethodField()

    class Meta(BugListSerializer.Meta):
        fields = BugListSerializer.Meta.fields + [
            "description", "submodule", "department", "site",
            "environment", "environment_label", "assigned_by", "assigned_date",
            "next_action", "hold_reason", "rejection_reason",
            "root_cause_type", "root_cause", "resolution", "resolved_date", "resolved_by",
            "tested_by", "verification_result", "verification_remarks", "verified_at",
            "closure_remarks", "closed_by", "closed_date", "closed_at",
            "last_reopened_at", "created_at", "updated_at",
            "allowed_transitions",
            "source_mail_id",
        ]

    def get_allowed_transitions(self, obj):
        """What this bug may become next.

        Served from the backend so the frontend action bar cannot drift from
        the authoritative state machine (spec 29).
        """
        from apps.bugs.services.workflow import allowed_targets

        return [
            {"value": s, "label": BugStatus(s).label}
            for s in allowed_targets(obj.status)
        ]

    def get_source_mail_id(self, obj):
        try:
            mail = obj.support_ticket.original_mail
        except Exception:
            return None
        if mail is None:
            return None
        return mail.message_id or mail.provider_message_id or None


class BugWriteSerializer(serializers.ModelSerializer):
    """Create/update payload, addressing masters by their public UUID."""

    project = serializers.SlugRelatedField(
        slug_field="unique_id", required=False, allow_null=True,
        queryset=ProjectMaster.objects.filter(is_deleted=False))
    module = serializers.SlugRelatedField(
        slug_field="unique_id", required=False, allow_null=True,
        queryset=ModuleMaster.objects.filter(is_deleted=False))
    submodule = serializers.SlugRelatedField(
        slug_field="unique_id", required=False, allow_null=True,
        queryset=SubmoduleMaster.objects.filter(is_deleted=False))
    priority = serializers.SlugRelatedField(
        slug_field="unique_id", required=False, allow_null=True,
        queryset=PriorityMaster.objects.filter(is_deleted=False))
    severity = serializers.SlugRelatedField(
        slug_field="unique_id", required=False, allow_null=True,
        queryset=SeverityMaster.objects.filter(is_deleted=False))
    department = serializers.SlugRelatedField(
        slug_field="unique_id", required=False, allow_null=True,
        queryset=DepartmentMaster.objects.filter(is_deleted=False))
    site = serializers.SlugRelatedField(
        slug_field="unique_id", required=False, allow_null=True,
        queryset=SiteMaster.objects.filter(is_deleted=False))
    root_cause_type = serializers.SlugRelatedField(
        slug_field="unique_id", required=False, allow_null=True,
        queryset=RootCauseTypeMaster.objects.filter(is_deleted=False))
    # Defaults to today in create_bug(); a reporter should not have to state
    # that they are reporting a bug today.
    reported_date = serializers.DateField(required=False)

    class Meta:
        model = Bug
        fields = [
            "project", "module", "submodule", "title", "description",
            "reported_date", "department", "site", "environment",
            "priority", "severity", "expected_closure_date",
            "root_cause_type", "root_cause", "resolution", "latest_remarks",
        ]

    def validate(self, attrs):
        """Keep the project -> module -> submodule chain internally consistent.

        Without this a client can post a module from a different project and
        produce a bug that no project-filtered report will ever show.
        """
        project = attrs.get("project") or getattr(self.instance, "project", None)
        module = attrs.get("module") or getattr(self.instance, "module", None)
        submodule = attrs.get("submodule") or getattr(self.instance, "submodule", None)

        if module and project and module.project_id != project.id:
            raise serializers.ValidationError(
                {"module": ["This module does not belong to the selected project."]}
            )
        if submodule and module and submodule.module_id != module.id:
            raise serializers.ValidationError(
                {"submodule": ["This submodule does not belong to the selected module."]}
            )
        if submodule and not module:
            raise serializers.ValidationError(
                {"module": ["Select a module before choosing a submodule."]}
            )
        return attrs
