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

    from apps.accounts.models import RolePermission

    codes = set(
        RolePermission.objects.filter(
            role__user_roles__user=user,
            role__user_roles__is_active=True,
            role__is_active=True,
            role__is_deleted=False,
            permission__is_active=True,
            permission__is_deleted=False,
        ).values_list("permission__codename", flat=True)
    )
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
