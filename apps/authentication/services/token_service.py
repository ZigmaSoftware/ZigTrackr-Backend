"""Token issuance and revocation."""

from rest_framework_simplejwt.tokens import RefreshToken

from apps.accounts.models import UserRole
from common.permissions.require import resolve_permission_codes


def issue_tokens(user):
    """Mint an access/refresh pair carrying identity claims.

    Roles and permissions are embedded so the frontend can render its menu
    without a second round trip. They are a convenience only -- every
    authorization decision is re-checked server-side against the database.
    """
    refresh = RefreshToken.for_user(user)
    refresh["username"] = user.username
    refresh["name"] = user.display_name
    refresh["unique_id"] = str(user.unique_id)
    return refresh, refresh.access_token


def revoke_refresh_token(raw_token):
    """Blacklist one refresh token. Returns True when it was actually revoked."""
    from rest_framework_simplejwt.exceptions import TokenError

    if not raw_token:
        return False
    try:
        RefreshToken(raw_token).blacklist()
        return True
    except TokenError:
        # Already expired, already blacklisted, or malformed. Logout is
        # idempotent, so this is not an error the caller needs to see.
        return False


def revoke_all_user_tokens(user):
    """Blacklist every outstanding refresh token for a user.

    Used on password change: a credential change must end other sessions,
    otherwise a stolen token survives the very action taken to stop it.
    """
    from rest_framework_simplejwt.token_blacklist.models import (
        BlacklistedToken,
        OutstandingToken,
    )

    count = 0
    for token in OutstandingToken.objects.filter(user=user):
        _, created = BlacklistedToken.objects.get_or_create(token=token)
        count += int(created)
    return count


def build_session_payload(user):
    """The /auth/me/ shape: identity, roles and resolved permissions."""
    roles = [
        {"code": ur.role.code, "name": ur.role.name}
        for ur in UserRole.objects.filter(user=user, is_active=True).select_related("role")
    ]
    return {
        "id": str(user.unique_id),
        "username": user.username,
        "name": user.display_name,
        "email": user.email,
        "employee_code": user.employee_code,
        "designation": user.designation,
        "department": user.department.name if user.department_id else None,
        "team": user.team.name if user.team_id else None,
        "site": user.site.name if user.site_id else None,
        "is_superuser": user.is_superuser,
        "must_change_password": user.must_change_password,
        "roles": roles,
        "permissions": sorted(resolve_permission_codes(user)),
    }
