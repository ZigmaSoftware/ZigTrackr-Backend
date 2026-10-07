"""Submodule bundles over the existing action-level RBAC catalog.

Changing one bundle must not expand unrelated, legacy partial grants. Runtime
permission checks, ownership rules and workflow state checks remain unchanged.
"""

from django.db import transaction
from rest_framework.exceptions import ValidationError

from apps.accounts.models import Permission, Role, RolePermission
from apps.audit.models import AuditAction
from common.permissions.codenames import ROLE_ADMIN
from common.permissions.ticket_submodules import TICKET_SUBMODULES
from common.services.audit import record_audit

PROTECTED_ADMIN_PERMISSIONS = frozenset({
    "admin.role.view", "admin.role.manage", "admin.permission.view", "admin.permission.manage",
})
SUBMODULE_LABELS = {"teams.workload": "Developer Workload", "teams.assignment": "Assignment Board"}


def permission_submodules(permissions):
    permissions = list(permissions)
    active_codes = {p.codename for p in permissions}
    groups = {}
    for permission in permissions:
        # Shared ticket action codes remain in the API for compatibility, but
        # are represented by actual sidebar screens rather than a broad row.
        if (permission.module, permission.screen_code) in {("tickets", "ticket"), ("bugs", "bug"), ("access", "request")}:
            continue
        screen = permission.screen_code or permission.codename.rsplit(".", 1)[0]
        key = f"{permission.module}.{screen}"
        group = groups.setdefault(key, {
            "key": key, "module": permission.module,
            "name": SUBMODULE_LABELS.get(key, permission.screen_name or screen),
            "permissions": [], "protected_roles": [],
        })
        group["permissions"].append(permission.codename)
        if permission.codename in PROTECTED_ADMIN_PERMISSIONS:
            group["protected_roles"] = [ROLE_ADMIN]
    for screen, name, module, _, actions in TICKET_SUBMODULES:
        key, gate = f"{module}.{screen}", f"tickets.{screen}.access"
        if gate in active_codes:
            groups[key].update(name=name, access_permission=gate,
                               permissions=[gate, *[code for code in actions if code in active_codes]])
    # The unified Daily Updates page needs both its historic bug-update gate
    # and ticket read access; disabling ticket menus must not break that page.
    if "bugs.update" in groups and "tickets.ticket.view" in active_codes:
        groups["bugs.update"]["permissions"].append("tickets.ticket.view")
    return groups


@transaction.atomic
def update_role_grants(*, role, actor, permissions=None, submodule_changes=None, request=None):
    if (permissions is None) == (submodule_changes is None):
        raise ValidationError("Provide either permissions or submodule_changes, not both.")
    # Lock the parent as well as its children: the empty-grant case otherwise
    # has no rows to lock, and two administrators can race on the same role.
    role = Role.objects.select_for_update().get(pk=role.pk)
    active_permissions = {p.codename: p for p in Permission.objects.filter(is_active=True, is_deleted=False)}
    existing = set(RolePermission.objects.filter(role=role).values_list("permission__codename", flat=True))
    if submodule_changes is not None:
        groups = permission_submodules(active_permissions.values())
        unknown = {change["key"] for change in submodule_changes} - groups.keys()
        if unknown:
            raise ValidationError({"submodule_changes": [f"Unknown submodule(s): {', '.join(sorted(unknown))}."]})
        wanted = set(existing)
        # Preserve actions shared with another accessible submodule. Disabling
        # one screen must not disable the work or chat on another selected one.
        disabled = {change["key"] for change in submodule_changes if not change["enabled"]}
        enabled = {change["key"] for change in submodule_changes if change["enabled"]}
        retained = set()
        for key, group in groups.items():
            if key in disabled:
                continue
            gate = group.get("access_permission")
            if key in enabled or (gate and gate in existing):
                # Preserve only existing actions on an untouched partial screen.
                retained.update(group["permissions"] if key in enabled else existing.intersection(group["permissions"]))
            elif not gate:
                retained.update(existing.intersection(group["permissions"]))
        for change in submodule_changes:
            codes = groups[change["key"]]["permissions"]
            if change["enabled"]:
                wanted.update(codes)
            else:
                wanted.difference_update(set(codes) - retained)
    else:
        wanted = set(permissions)
        unknown = wanted - active_permissions.keys()
        if unknown:
            raise ValidationError({"permissions": [f"Unknown permission(s): {', '.join(sorted(unknown))}."]})
    if role.code == ROLE_ADMIN and not PROTECTED_ADMIN_PERMISSIONS.issubset(wanted):
        raise ValidationError({"permissions": ["The Admin role must keep role and permission management access."]})

    added, removed = wanted - existing, existing - wanted
    if removed:
        RolePermission.objects.filter(role=role, permission__codename__in=removed).delete()
    RolePermission.objects.bulk_create([
        RolePermission(role=role, permission=active_permissions[code], created_by=getattr(actor, "unique_id", None))
        for code in sorted(added)
    ])
    record_audit(
        action=AuditAction.PERMISSION_CHANGED, entity=role, actor=actor,
        old_value=", ".join(sorted(existing)), new_value=", ".join(sorted(wanted)),
        remarks="Submodule access updated." if submodule_changes is not None else "Role permission grants updated.",
        metadata={"added": sorted(added), "removed": sorted(removed),
                  "submodule_changes": submodule_changes or []}, request=request,
    )
