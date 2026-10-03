"""Payload serializers for the workflow action endpoints (spec 42)."""

from django.contrib.auth import get_user_model
from rest_framework import serializers

from apps.bugs.constants import BugStatus, Environment, TestResult, VerificationResult
from apps.masters.models import RootCauseTypeMaster
from apps.masters.models import (
    DepartmentMaster, ModuleMaster, PriorityMaster, ProjectMaster,
    SeverityMaster, SiteMaster, SubmoduleMaster,
)

User = get_user_model()


class AssignSerializer(serializers.Serializer):
    owner = serializers.SlugRelatedField(
        slug_field="unique_id",
        queryset=User.objects.filter(is_active=True, is_deleted=False),
    )
    remarks = serializers.CharField(required=False, allow_blank=True, default="")
    expected_closure_date = serializers.DateField(required=False, allow_null=True)


class EmailBugAssignmentSerializer(serializers.Serializer):
    """The routing fields required to move an email-created bug into work."""

    project = serializers.SlugRelatedField(
        slug_field="unique_id", queryset=ProjectMaster.objects.filter(is_deleted=False)
    )
    module = serializers.SlugRelatedField(
        slug_field="unique_id", required=False, allow_null=True,
        queryset=ModuleMaster.objects.filter(is_deleted=False)
    )
    submodule = serializers.SlugRelatedField(
        slug_field="unique_id", required=False, allow_null=True,
        queryset=SubmoduleMaster.objects.filter(is_deleted=False)
    )
    priority = serializers.SlugRelatedField(
        slug_field="unique_id", queryset=PriorityMaster.objects.filter(is_deleted=False)
    )
    severity = serializers.SlugRelatedField(
        slug_field="unique_id", queryset=SeverityMaster.objects.filter(is_deleted=False)
    )
    environment = serializers.ChoiceField(
        choices=Environment.choices, required=False, allow_null=True
    )
    department = serializers.SlugRelatedField(
        slug_field="unique_id", required=False, allow_null=True,
        queryset=DepartmentMaster.objects.filter(is_deleted=False)
    )
    site = serializers.SlugRelatedField(
        slug_field="unique_id", required=False, allow_null=True,
        queryset=SiteMaster.objects.filter(is_deleted=False)
    )
    reported_date = serializers.DateField(required=False, allow_null=True)
    expected_closure_date = serializers.DateField(required=False, allow_null=True)
    owner = serializers.SlugRelatedField(
        slug_field="unique_id", queryset=User.objects.filter(is_active=True, is_deleted=False)
    )
    remarks = serializers.CharField(required=False, allow_blank=True, default="")

    def validate(self, attrs):
        project = attrs["project"]
        module = attrs.get("module")
        submodule = attrs.get("submodule")
        if module and module.project_id != project.id:
            raise serializers.ValidationError({"module": "Module belongs to another project."})
        if submodule and (not module or submodule.module_id != module.id):
            raise serializers.ValidationError({"submodule": "Submodule does not belong to the selected module."})
        return attrs


class StatusChangeSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=BugStatus.choices)
    remarks = serializers.CharField(required=False, allow_blank=True, default="")
    # Supplied when the target status requires them (spec 30).
    hold_reason = serializers.CharField(required=False, allow_blank=True)
    rejection_reason = serializers.CharField(required=False, allow_blank=True)


class TestingSerializer(serializers.Serializer):
    test_result = serializers.ChoiceField(choices=TestResult.choices)
    test_remarks = serializers.CharField(required=False, allow_blank=True, default="")


class ResolveSerializer(serializers.Serializer):
    root_cause = serializers.CharField()
    resolution = serializers.CharField()
    root_cause_type = serializers.SlugRelatedField(
        slug_field="unique_id", required=False, allow_null=True,
        queryset=RootCauseTypeMaster.objects.filter(is_deleted=False),
    )
    resolved_date = serializers.DateField(required=False, allow_null=True)
    remarks = serializers.CharField(required=False, allow_blank=True, default="")


class CloseSerializer(serializers.Serializer):
    closure_remarks = serializers.CharField()
    verification_result = serializers.ChoiceField(
        choices=VerificationResult.choices, required=False, allow_blank=True,
    )
    closed_date = serializers.DateField(required=False, allow_null=True)
    remarks = serializers.CharField(required=False, allow_blank=True, default="")


class ReopenSerializer(serializers.Serializer):
    reopen_reason = serializers.CharField()
