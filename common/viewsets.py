"""Shared viewset behaviour."""

from rest_framework import viewsets
from rest_framework.permissions import IsAuthenticated

from common.permissions.require import RequirePermission
from common.responses import EnvelopeMessageMixin


class AuditedSoftDeleteViewSet(EnvelopeMessageMixin, viewsets.ModelViewSet):
    """CRUD over a BaseMaster subclass with soft delete and audit stamping.

    Every master screen in the app shares this, so spec 3's "do not create
    different implementations for the same repeated UI pattern" holds on the
    backend too: adding a master is a serializer plus three lines of viewset.
    """

    permission_classes = [IsAuthenticated]
    lookup_field = "unique_id"
    lookup_url_kwarg = "unique_id"

    def get_queryset(self):
        qs = self.queryset
        if not self._include_inactive():
            qs = qs.filter(is_active=True)
        return qs.filter(is_deleted=False)

    def _include_inactive(self):
        value = self.request.query_params.get("include_inactive", "")
        return value.lower() in ("1", "true", "yes")

    def perform_create(self, serializer):
        serializer.save(created_by=getattr(self.request.user, "unique_id", None),
                        updated_by=getattr(self.request.user, "unique_id", None))

    def perform_update(self, serializer):
        serializer.save(updated_by=getattr(self.request.user, "unique_id", None))

    def perform_destroy(self, instance):
        instance.soft_delete(deleted_by=getattr(self.request.user, "unique_id", None))


def master_permissions(view_code="masters.master.view", write_code="masters.master.add",
                       edit_code="masters.master.edit", delete_code="masters.master.delete"):
    """Per-action permission map for master viewsets."""
    return {
        "list": [RequirePermission(view_code)],
        "retrieve": [RequirePermission(view_code)],
        "create": [RequirePermission(write_code)],
        "update": [RequirePermission(edit_code)],
        "partial_update": [RequirePermission(edit_code)],
        "destroy": [RequirePermission(delete_code)],
    }


class PermissionByActionMixin:
    """Resolve permission classes per action from `permission_map`.

    An action with no entry in `permission_map` falls through to
    `permission_classes` on the class (IsAuthenticated on every viewset that
    currently uses this mixin) -- i.e. any signed-in user, not "no one".
    This is a fail-open default: a codename gate silently omitted from
    `permission_map` does not error, it just grants access to every
    authenticated user. When adding a new @action to a viewset that uses this
    mixin, add its entry to permission_map even if the row-level check alone
    would be sufficient -- an explicit codename entry survives someone later
    relaxing the row-level check in a way that assumed the codename gate was
    also there. This is not a hard failure by design: some read-only, fully
    row-scoped views (NotificationViewSet) deliberately rely on
    IsAuthenticated + scoping alone and never set permission_map at all --
    that is a valid pattern, not an oversight, as long as it is a conscious
    choice.
    """

    permission_map: dict = {}

    def get_permissions(self):
        classes = self.permission_map.get(getattr(self, "action", None))
        if classes:
            return [IsAuthenticated()] + [cls() for cls in classes]
        return super().get_permissions()
