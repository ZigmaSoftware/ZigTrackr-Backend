"""Permission resolution and the DRF gate."""

from rest_framework.permissions import BasePermission

_CACHE_ATTR = "_zbt_permission_codes"


def resolve_permission_codes(user):
    """Return the set of permission codenames granted to `user` via their roles.

    Cached on the user instance for the lifetime of the request: a list endpoint
    may consult permissions once per row during scoping and serialization, and
    re-querying the join each time would be a needless N+1.
    """
    if user is None or not getattr(user, "is_authenticated", False):
        return set()

    cached = getattr(user, _CACHE_ATTR, None)
    if cached is not None:
        return cached

    from apps.accounts.models import Role
    from apps.accounts.services.company_roles import role_settings

    roles = list(Role.objects.filter(user_roles__user=user, user_roles__is_active=True,
                                    is_active=True, is_deleted=False).distinct())
    grants, overrides = role_settings(roles, getattr(user, "company", None))
    codes = set()
    for role in roles:
        if role.pk not in overrides or overrides[role.pk].is_active:
            codes.update(grants[role.pk])
    setattr(user, _CACHE_ATTR, codes)
    return codes


def has_permission(user, codename):
    if user is None or not getattr(user, "is_authenticated", False):
        return False
    if getattr(user, "is_superuser", False):
        return True
    return codename in resolve_permission_codes(user)


def RequirePermission(*codenames):
    """Permission class factory. Passing several codenames means ANY of them.

    Usage:  permission_classes = [RequirePermission("bugs.bug.assign")]
    """

    class _RequirePermission(BasePermission):
        message = "You do not have permission to perform this action."

        def has_permission(self, request, view):
            user = request.user
            if not getattr(user, "is_authenticated", False):
                return False
            return any(has_permission(user, code) for code in codenames)

    _RequirePermission.__name__ = f"RequirePermission_{'_'.join(codenames)}"
    return _RequirePermission


def user_ids_with_permission(*codenames):
    """Primary keys of active users holding ANY of these codenames.

    The inverse of has_permission, for fan-out: broadcasting needs the set of
    recipients up front rather than a yes/no for one user. Superusers are
    included because has_permission short-circuits to True for them.
    """
    from apps.accounts.models import User
    from apps.accounts.models import Role
    from apps.accounts.services.company_roles import role_settings
    from common.permissions.company import can_access_company

    users = list(User.objects.filter(is_active=True, is_deleted=False).select_related("company")
                 .prefetch_related("user_roles__role"))
    roles = list(Role.objects.filter(is_active=True, is_deleted=False))
    company_grants = {}
    result = set()
    for user in users:
        if user.is_superuser:
            result.add(user.pk)
            continue
        if not can_access_company(user, user):
            continue
        if user.company_id not in company_grants:
            company_grants[user.company_id] = role_settings(roles, user.company)
        grants, overrides = company_grants[user.company_id]
        for assignment in user.user_roles.all():
            override = overrides.get(assignment.role_id)
            if (assignment.is_active and assignment.role.is_active and not assignment.role.is_deleted
                    and (not override or override.is_active)
                    and set(codenames).intersection(grants.get(assignment.role_id, set()))):
                result.add(user.pk)
                break
    return result
