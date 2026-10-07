"""User, role and permission endpoints (spec 16 Administration)."""

from django.contrib.auth import get_user_model
from django.db.models import Count
from django.db.models import Prefetch
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from apps.accounts.models import Permission, Role, RolePermission, UserRole
from apps.accounts.services.permission_service import permission_submodules, update_role_grants
from apps.accounts.serializers import (
    PermissionSerializer,
    RoleLiteSerializer,
    RolePermissionUpdateSerializer,
    RoleWriteSerializer,
    UserLiteSerializer,
    UserPasswordResetSerializer,
    UserSerializer,
    UserWriteSerializer,
)
from apps.audit.models import AuditAction
from apps.accounts.services.user_service import (
    create_user,
    deactivate_user,
    reactivate_user,
    reset_password,
    update_user,
)
from common.permissions.require import RequirePermission
from common.responses import EnvelopeMessageMixin, created, ok
from common.services.audit import record_field_changes
from common.viewsets import PermissionByActionMixin

User = get_user_model()


class UserViewSet(PermissionByActionMixin, EnvelopeMessageMixin, viewsets.ModelViewSet):
    """User directory and administration (spec 16).

    Create/edit/deactivate/reactivate/reset-password all route through
    apps.accounts.services.user_service, never through direct model writes --
    that service layer is what guarantees a new user gets a validated
    password, at least the chance to hold a role, and an audit trail, none of
    which a `User.objects.create()` in a shell session provides.
    """

    lookup_field = "unique_id"
    lookup_url_kwarg = "unique_id"
    permission_classes = [IsAuthenticated]
    search_fields = ["full_name", "username", "email", "employee_code"]
    ordering_fields = ["full_name", "username"]
    ordering = ["full_name"]

    permission_map = {
        "list": [RequirePermission("admin.user.view")],
        "retrieve": [RequirePermission("admin.user.view")],
        "create": [RequirePermission("admin.user.add")],
        "update": [RequirePermission("admin.user.edit")],
        "partial_update": [RequirePermission("admin.user.edit")],
        "destroy": [RequirePermission("admin.user.delete")],
        "reactivate": [RequirePermission("admin.user.edit")],
        "reset_password": [RequirePermission("admin.user.edit")],
        # Anyone who can assign or create a bug needs the assignment dropdown;
        # it existing with no entry here meant it fell through to the
        # PermissionByActionMixin default of IsAuthenticated only, i.e. any
        # signed-in user could enumerate the whole assignable directory.
        "assignable": [RequirePermission(
            "bugs.bug.assign", "bugs.bug.add", "teams.assignment.use",
            "tickets.ticket.assign",
        )],
    }

    envelope_messages = {
        "create": "User created successfully.",
        "update": "User updated successfully.",
        "partial_update": "User updated successfully.",
        "destroy": "User deactivated successfully.",
    }

    def get_serializer_class(self):
        if self.action in ("create", "update", "partial_update"):
            return UserWriteSerializer
        return UserSerializer

    def get_queryset(self):
        qs = (User.objects.filter(is_deleted=False)
              .select_related("department", "team", "site")
              .prefetch_related(Prefetch(
                  "user_roles",
                  queryset=UserRole.objects.filter(is_active=True).select_related("role"))))
        if self.request.query_params.get("include_inactive", "").lower() not in ("1", "true"):
            qs = qs.filter(is_active=True)
        team = self.request.query_params.get("team")
        if team:
            qs = qs.filter(team__unique_id=team)
        role = self.request.query_params.get("role")
        if role:
            qs = qs.filter(user_roles__role__code=role, user_roles__is_active=True)
        return qs

    def create(self, request, *args, **kwargs):
        serializer = UserWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = dict(serializer.validated_data)
        password = data.pop("password")
        roles = data.pop("roles", [])
        user = create_user(actor=request.user, request=request, password=password,
                           roles=roles, **data)
        return created(UserSerializer(user).data, message="User created successfully.")

    def update(self, request, *args, **kwargs):
        user = self.get_object()
        partial = kwargs.pop("partial", False)
        serializer = UserWriteSerializer(user, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)
        data = dict(serializer.validated_data)
        data.pop("password", None)  # never set via the profile-edit path
        roles = data.pop("roles", None)
        user = update_user(user=user, actor=request.user, request=request, roles=roles, **data)
        return ok(UserSerializer(user).data, message="User updated successfully.")

    def destroy(self, request, *args, **kwargs):
        user = self.get_object()
        if user.pk == request.user.pk:
            from rest_framework.exceptions import ValidationError

            raise ValidationError({"detail": ["You cannot deactivate your own account."]})
        deactivate_user(user=user, actor=request.user, request=request)
        return ok(None, message="User deactivated successfully.")

    @action(detail=True, methods=["post"])
    def reactivate(self, request, unique_id=None):
        from django.shortcuts import get_object_or_404

        # get_object() runs against get_queryset(), which filters out
        # is_deleted users -- a deactivated account can never be fetched
        # through the normal detail lookup, deliberately, so reactivation
        # looks it up directly instead.
        user = get_object_or_404(User, unique_id=unique_id, is_deleted=True)
        user = reactivate_user(user=user, actor=request.user, request=request)
        return ok(UserSerializer(user).data, message="User reactivated successfully.")

    @action(detail=True, methods=["post"], url_path="reset-password")
    def reset_password(self, request, unique_id=None):
        user = self.get_object()
        serializer = UserPasswordResetSerializer(
            data=request.data, context={"target_user": user})
        serializer.is_valid(raise_exception=True)
        reset_password(
            user=user, actor=request.user, request=request,
            new_password=serializer.validated_data["new_password"],
            require_change_at_login=serializer.validated_data["require_change_at_login"],
        )
        return ok(None, message="Password reset successfully.")

    ASSIGNABLE_ROLES = ["DEVELOPER", "TEAM_LEAD", "TESTER"]

    @action(detail=False, methods=["get"])
    def assignable(self, request):
        """Users who can own a bug -- the assignment dropdown.

        `?roles=DEVELOPER,TEAM_LEAD` narrows the list for callers that route
        work rather than verify it. Unknown codes are dropped rather than
        rejected, so a stale client cannot empty its own dropdown.
        """
        requested = [
            code.strip().upper()
            for code in (request.query_params.get("roles") or "").split(",")
            if code.strip()
        ]
        roles = [code for code in requested if code in self.ASSIGNABLE_ROLES]
        qs = self.get_queryset().filter(
            user_roles__role__code__in=roles or self.ASSIGNABLE_ROLES,
            user_roles__is_active=True,
        ).distinct()
        return ok(UserLiteSerializer(qs, many=True).data, message="Assignable users retrieved.")


class RoleViewSet(PermissionByActionMixin, EnvelopeMessageMixin, viewsets.ModelViewSet):
    serializer_class = RoleLiteSerializer
    lookup_field = "unique_id"
    lookup_url_kwarg = "unique_id"
    permission_classes = [IsAuthenticated]
    permission_map = {
        "list": [RequirePermission("admin.role.view")],
        "retrieve": [RequirePermission("admin.role.view")],
        "update": [RequirePermission("admin.role.manage")],
        "partial_update": [RequirePermission("admin.role.manage")],
        "destroy": [RequirePermission("admin.role.manage")],
        "permissions": [RequirePermission("admin.permission.manage")],
    }
    envelope_messages = {
        "update": "Role updated.",
        "partial_update": "Role updated.",
        "permissions": "Role permissions updated.",
    }

    def get_queryset(self):
        return (
            Role.objects
            .filter(is_deleted=False)
            .annotate(permission_count=Count("role_permissions", distinct=True))
            .order_by("rank", "name")
        )

    def get_serializer_class(self):
        if self.action in ("update", "partial_update"):
            return RoleWriteSerializer
        return RoleLiteSerializer

    def update(self, request, *args, **kwargs):
        role = self.get_object()
        serializer = RoleWriteSerializer(
            role, data=request.data, partial=kwargs.pop("partial", False)
        )
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        if role.is_system and data.get("is_active") is False:
            raise ValidationError({"is_active": ["System roles cannot be deactivated."]})
        changes = {}
        for field, value in data.items():
            old = getattr(role, field)
            if old != value:
                changes[field] = (old, value)
                setattr(role, field, value)
        if changes:
            role.updated_by = getattr(request.user, "unique_id", None)
            role.save()
            record_field_changes(
                entity=role,
                actor=request.user,
                changes=changes,
                action=AuditAction.ROLE_UPDATED,
                request=request,
            )
        refreshed = self.get_queryset().get(pk=role.pk)
        return ok(RoleLiteSerializer(refreshed).data, message="Role updated.")

    def destroy(self, request, *args, **kwargs):
        raise ValidationError({"detail": ["Roles cannot be deleted from this screen."]})

    @action(detail=True, methods=["put"])
    def permissions(self, request, unique_id=None):
        role = self.get_object()
        serializer = RolePermissionUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        update_role_grants(role=role, actor=request.user, request=request,
                           **serializer.validated_data)
        return ok(self._matrix_payload(), message="Role permissions updated.")

    @staticmethod
    def _matrix_payload():
        permissions = list(Permission.objects.filter(is_active=True, is_deleted=False))
        roles = list(Role.objects.filter(is_active=True, is_deleted=False).order_by("rank", "name"))
        grants = set(
            RolePermission.objects.values_list("role__code", "permission__codename")
        )
        modules = {}
        for perm in permissions:
            modules.setdefault(perm.module, []).append({
                "codename": perm.codename,
                "name": perm.name,
                "screen": perm.screen_name,
                "action": perm.action,
                "roles": {r.code: (r.code, perm.codename) in grants for r in roles},
            })
        submodules = permission_submodules(permissions)
        # Action cells may be shared by multiple screens; keep each codename
        # once, while placing the former generic ticket cells in their group.
        shared_tickets = modules.pop("tickets", [])
        modules.setdefault("ticket_management", []).extend(shared_tickets)
        return {
            "roles": [
                {
                    "id": str(r.unique_id),
                    "code": r.code,
                    "name": r.name,
                    "is_system": r.is_system,
                }
                for r in roles
            ],
            "modules": [{"module": m, "permissions": p,
                         "submodules": [{k: v for k, v in group.items() if k != "module"}
                                        for group in submodules.values() if group["module"] == m]}
                        for m, p in modules.items()],
        }


class PermissionViewSet(PermissionByActionMixin, viewsets.ReadOnlyModelViewSet):
    serializer_class = PermissionSerializer
    lookup_field = "unique_id"
    lookup_url_kwarg = "unique_id"
    permission_classes = [IsAuthenticated]
    permission_map = {
        "list": [RequirePermission("admin.permission.view")],
        "retrieve": [RequirePermission("admin.permission.view")],
    }

    def get_queryset(self):
        return Permission.objects.filter(is_deleted=False).order_by(
            "sort_order", "module", "screen_code", "codename",
        )


class PermissionMatrixView(APIView):
    """Role x permission matrix for the Permission Management screen."""

    permission_classes = [IsAuthenticated, RequirePermission("admin.permission.view")]

    def get(self, request):
        return ok(RoleViewSet._matrix_payload(), message="Permission matrix retrieved.")
