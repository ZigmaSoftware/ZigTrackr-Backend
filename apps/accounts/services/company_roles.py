"""Resolve global defaults and explicit company role snapshots."""

import logging

from rest_framework.exceptions import PermissionDenied, ValidationError

from apps.accounts.models import CompanyRole, RolePermission
from common.permissions.company import is_super_admin, require_company_access

logger = logging.getLogger(__name__)


def administration_company(user, company_uuid=None):
    from uuid import UUID
    from apps.masters.models import CompanyMaster

    if company_uuid:
        try:
            value = UUID(str(company_uuid))
        except (ValueError, TypeError, AttributeError):
            raise ValidationError({"company": ["Select a valid company."]}) from None
        company = CompanyMaster.objects.filter(unique_id=value, is_deleted=False).first()
        if company is None:
            raise ValidationError({"company": ["No such company."]})
        require_company_access(user, company)
        return company
    if is_super_admin(user):
        return None
    require_company_access(user, user)
    return user.company


def role_settings(roles, company=None):
    roles = list(roles)
    grants = {role.pk: set() for role in roles}
    for role_id, code in RolePermission.objects.filter(role__in=roles,
        permission__is_active=True, permission__is_deleted=False).values_list("role_id", "permission__codename"):
        grants[role_id].add(code)
    overrides = {}
    if company is not None:
        overrides = {row.role_id: row for row in CompanyRole.objects.filter(company=company, role__in=roles,
            is_deleted=False).prefetch_related("permissions")}
        for role_id, override in overrides.items():
            grants[role_id] = {permission.codename for permission in override.permissions.all()
                               if permission.is_active and not permission.is_deleted}
    return grants, overrides


def editable_role(user, role):
    return is_super_admin(user) or role.code != "ADMIN"


def require_role_management(user, role, *, company=None, codename="admin.permission.manage"):
    from common.permissions.require import has_permission

    if not has_permission(user, codename):
        raise PermissionDenied("You do not have permission to manage this role.")
    if not editable_role(user, role):
        raise PermissionDenied("Only Super Admin can change Admin access.")
    if company is None and not is_super_admin(user):
        raise PermissionDenied("Only Super Admin can change global role defaults.")
    if company is not None:
        require_company_access(user, company)


def lock_company_role(role, company, actor):
    # The caller holds the Role lock, serializing creation of an override too.
    row, created = CompanyRole.objects.select_for_update().get_or_create(company=company, role=role,
        defaults={"name": role.name, "description": role.description, "is_active": role.is_active,
                  "created_by": actor.unique_id, "updated_by": actor.unique_id})
    if created:
        row.permissions.set(role.role_permissions.values_list("permission_id", flat=True))
    return row


def publish_role_access_changed(role_id, company_id=None):
    from asgiref.sync import async_to_sync
    from channels.layers import get_channel_layer
    from apps.accounts.models import User

    users = User.objects.filter(is_active=True, is_deleted=False,
        user_roles__role_id=role_id, user_roles__is_active=True)
    if company_id is not None:
        users = users.filter(company_id=company_id)
    layer = get_channel_layer()
    if layer is None:
        return
    for user_id in users.values_list("pk", flat=True).distinct():
        try:
            async_to_sync(layer.group_send)(f"user_inbox_{user_id}", {"type": "inbox.permissions_changed"})
        except Exception:
            logger.exception("Could not notify user %s of an access change", user_id)
