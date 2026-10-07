"""User serializers."""

from collections.abc import Mapping

from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers

from apps.accounts.models import Permission, Role, UserRole
from apps.accounts.services.user_service import validate_user_profile_fields

User = get_user_model()


class UserLiteSerializer(serializers.ModelSerializer):
    """Minimal user shape for embedding in bug rows and dropdowns."""

    id = serializers.UUIDField(source="unique_id", read_only=True)
    name = serializers.CharField(source="display_name", read_only=True)

    class Meta:
        model = User
        fields = ["id", "username", "name", "email"]


class RoleLiteSerializer(serializers.ModelSerializer):
    id = serializers.UUIDField(source="unique_id", read_only=True)
    permission_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = Role
        fields = [
            "id", "code", "name", "description", "is_active", "is_system",
            "permission_count",
        ]


class RoleWriteSerializer(serializers.ModelSerializer):
    class Meta:
        model = Role
        fields = ["name", "description", "is_active"]


class PermissionSerializer(serializers.ModelSerializer):
    id = serializers.UUIDField(source="unique_id", read_only=True)

    class Meta:
        model = Permission
        fields = [
            "id", "codename", "name", "module", "screen_code", "screen_name",
            "action", "sort_order", "is_active",
        ]
        read_only_fields = fields


class SubmoduleChangeSerializer(serializers.Serializer):
    key = serializers.CharField(max_length=200)
    enabled = serializers.BooleanField()


class RolePermissionUpdateSerializer(serializers.Serializer):
    permissions = serializers.ListField(
        child=serializers.CharField(), allow_empty=True, required=False,
    )
    submodule_changes = SubmoduleChangeSerializer(many=True, required=False, allow_empty=False)

    def validate(self, attrs):
        if ("permissions" in attrs) == ("submodule_changes" in attrs):
            raise serializers.ValidationError("Provide permissions or submodule_changes, not both.")
        changes = attrs.get("submodule_changes", [])
        keys = [change["key"] for change in changes]
        if len(keys) != len(set(keys)):
            raise serializers.ValidationError({"submodule_changes": ["Each submodule may appear only once."]})
        return attrs


class UserSerializer(serializers.ModelSerializer):
    id = serializers.UUIDField(source="unique_id", read_only=True)
    name = serializers.CharField(source="display_name", read_only=True)
    roles = serializers.SerializerMethodField()
    department_name = serializers.CharField(source="department.name", read_only=True, default=None)
    team_name = serializers.CharField(source="team.name", read_only=True, default=None)
    site_name = serializers.CharField(source="site.name", read_only=True, default=None)

    class Meta:
        model = User
        fields = [
            "id", "username", "name", "full_name", "email", "employee_code",
            "phone", "designation", "department_name", "team_name", "site_name",
            "is_active", "roles", "last_login", "date_joined",
        ]

    def get_roles(self, obj):
        return [
            {"code": ur.role.code, "name": ur.role.name}
            for ur in obj.user_roles.all() if ur.is_active
        ]


class UserWriteSerializer(serializers.ModelSerializer):
    """Create/update payload for the User Management screen (spec 16).

    A password is required on create and forbidden on update -- resetting a
    password is a separate, explicit action (UserPasswordResetSerializer),
    never a silent side effect of an unrelated profile edit.
    """

    id = serializers.UUIDField(source="unique_id", read_only=True)
    password = serializers.CharField(write_only=True, required=False, min_length=8)
    # Role codes rather than role UUIDs: the caller picks from the same fixed
    # list ROLE_DEFINITIONS seeds, and a code is what every other part of this
    # codebase (ROLE_PERMISSIONS, seed_demo_users) already keys on.
    roles = serializers.ListField(
        child=serializers.CharField(), required=False, allow_empty=True, write_only=True,
    )

    class Meta:
        model = User
        fields = [
            "id", "username", "email", "password", "full_name", "employee_code",
            "phone", "is_active", "roles",
        ]

    def to_internal_value(self, data):
        # DRF normally ignores undeclared fields. Reject retired inputs
        # explicitly so a stale client cannot report that these were saved.
        try:
            if isinstance(data, Mapping):
                validate_user_profile_fields(data)
        except DjangoValidationError as exc:
            raise serializers.ValidationError(exc.message_dict) from exc
        return super().to_internal_value(data)

    def validate_username(self, value):
        qs = User.objects.filter(username__iexact=value, is_deleted=False)
        if self.instance is not None:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise serializers.ValidationError("A user with this username already exists.")
        return value

    def validate_email(self, value):
        if not value:
            return value
        qs = User.objects.filter(email__iexact=value, is_deleted=False)
        if self.instance is not None:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise serializers.ValidationError("A user with this email already exists.")
        return value

    def validate_password(self, value):
        # Django's validators need a user instance to run
        # UserAttributeSimilarityValidator meaningfully; pass what we have so
        # far (self.instance on update, None on create -- the similarity
        # check simply skips itself without one).
        try:
            validate_password(value, user=self.instance)
        except DjangoValidationError as exc:
            raise serializers.ValidationError(list(exc.messages))
        return value

    def validate_roles(self, value):
        codes = set(value)
        valid = set(Role.objects.filter(code__in=codes, is_active=True, is_deleted=False)
                    .values_list("code", flat=True))
        unknown = codes - valid
        if unknown:
            raise serializers.ValidationError(f"Unknown role code(s): {', '.join(sorted(unknown))}.")
        return value

    def validate(self, attrs):
        if self.instance is None and "password" not in attrs:
            raise serializers.ValidationError(
                {"password": ["A password is required when creating a user."]}
            )
        return attrs


class UserPasswordResetSerializer(serializers.Serializer):
    """An admin setting someone else's password.

    Deliberately separate from the self-service PasswordChangeSerializer in
    apps/authentication: that one verifies the caller's *current* password,
    this one is an administrative override and verifies nothing about the
    target account -- only the caller's admin.user.edit permission gates it.
    """

    new_password = serializers.CharField(write_only=True, min_length=8)
    require_change_at_login = serializers.BooleanField(default=True)

    def validate_new_password(self, value):
        try:
            validate_password(value, user=self.context.get("target_user"))
        except DjangoValidationError as exc:
            raise serializers.ValidationError(list(exc.messages))
        return value
