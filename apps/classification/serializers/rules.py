"""Classification and mapping rule serializers.

Uniqueness is enforced here rather than by a database constraint: it must apply
only to live rows, and MariaDB silently ignores a conditional UniqueConstraint
(models.W036 is a warning and nothing is created). This mirrors UniqueNameMixin
in apps/masters/serializers/masters.py, which exists for the same reason.
"""

from rest_framework import serializers

from apps.classification.models import ClassificationRule, ModuleMappingRule
from apps.masters.models import ModuleMaster, ProjectMaster, SubmoduleMaster
from apps.classification.services.normalize import normalize_text

AUDIT_READ_ONLY = ["id", "created_at", "updated_at", "is_deleted", "is_system"]

# Changing these on a seeded rule would silently redefine what the system rule
# means; score and is_active stay editable, because tuning is the point.
SYSTEM_LOCKED_FIELDS = ("pattern", "rule_type", "ticket_type")


class ClassificationRuleSerializer(serializers.ModelSerializer):
    id = serializers.UUIDField(source="unique_id", read_only=True)

    class Meta:
        model = ClassificationRule
        fields = [
            "id", "ticket_type", "rule_type", "pattern", "score",
            "priority_order", "notes", "is_active", "is_system",
            "created_at", "updated_at", "is_deleted",
        ]
        read_only_fields = AUDIT_READ_ONLY

    def validate_score(self, value):
        if not 0 <= value <= 100:
            raise serializers.ValidationError("Score must be between 0 and 100.")
        return value

    def validate(self, attrs):
        instance = self.instance

        if instance is not None and instance.is_system:
            for field in SYSTEM_LOCKED_FIELDS:
                if field in attrs and attrs[field] != getattr(instance, field):
                    raise serializers.ValidationError(
                        {field: ["This field cannot be changed on a system rule."]}
                    )

        ticket_type = attrs.get("ticket_type") or getattr(instance, "ticket_type", None)
        rule_type = attrs.get("rule_type") or getattr(instance, "rule_type", None)
        pattern = attrs.get("pattern") or getattr(instance, "pattern", "")

        if pattern:
            duplicates = ClassificationRule.objects.filter(
                ticket_type=ticket_type,
                rule_type=rule_type,
                normalized_pattern=normalize_text(pattern),
                is_deleted=False,
            )
            if instance is not None:
                duplicates = duplicates.exclude(pk=instance.pk)
            if duplicates.exists():
                raise serializers.ValidationError(
                    {"pattern": ["An identical rule already exists for this ticket type."]}
                )
        return attrs


class ModuleMappingRuleSerializer(serializers.ModelSerializer):
    id = serializers.UUIDField(source="unique_id", read_only=True)
    project = serializers.SlugRelatedField(
        slug_field="unique_id",
        queryset=ProjectMaster.objects.filter(is_deleted=False),
        required=True,
    )
    module = serializers.SlugRelatedField(
        slug_field="unique_id",
        queryset=ModuleMaster.objects.filter(is_deleted=False),
        required=False, allow_null=True,
    )
    submodule = serializers.SlugRelatedField(
        slug_field="unique_id",
        queryset=SubmoduleMaster.objects.filter(is_deleted=False),
        required=False, allow_null=True,
    )
    project_name = serializers.CharField(source="project.name", read_only=True)
    module_name = serializers.CharField(source="module.name", default="", read_only=True)
    submodule_name = serializers.CharField(source="submodule.name", default="", read_only=True)

    class Meta:
        model = ModuleMappingRule
        fields = [
            "id", "keyword_or_phrase", "project", "module", "submodule",
            "project_name", "module_name", "submodule_name",
            "score", "priority_order", "is_active", "is_system",
            "created_at", "updated_at", "is_deleted",
        ]
        read_only_fields = AUDIT_READ_ONLY

    def validate(self, attrs):
        instance = self.instance
        project = attrs.get("project") or getattr(instance, "project", None)
        module = attrs.get("module") or getattr(instance, "module", None)
        submodule = attrs.get("submodule") or getattr(instance, "submodule", None)

        # A rule pointing across projects routes tickets to the wrong team, and
        # the ticket still looks correctly assigned.
        if module and project and module.project_id != project.pk:
            raise serializers.ValidationError(
                {"module": ["Module does not belong to the selected project."]}
            )
        if submodule and module and submodule.module_id != module.pk:
            raise serializers.ValidationError(
                {"submodule": ["Submodule does not belong to the selected module."]}
            )

        phrase = attrs.get("keyword_or_phrase") or getattr(instance, "keyword_or_phrase", "")
        if phrase and project:
            duplicates = ModuleMappingRule.objects.filter(
                normalized_phrase=normalize_text(phrase),
                project=project,
                is_deleted=False,
            )
            if instance is not None:
                duplicates = duplicates.exclude(pk=instance.pk)
            if duplicates.exists():
                raise serializers.ValidationError(
                    {"keyword_or_phrase": ["This phrase is already mapped for this project."]}
                )
        return attrs


class RuleTestSerializer(serializers.Serializer):
    """Try a subject and body against the live rules, persisting nothing."""

    subject = serializers.CharField(required=False, allow_blank=True)
    body = serializers.CharField(required=False, allow_blank=True)

    def validate(self, attrs):
        if not (attrs.get("subject") or attrs.get("body")):
            raise serializers.ValidationError("Provide a subject or a body to test.")
        return attrs
