"""User administration (spec 16).

This is the one place users are created, edited, deactivated, reactivated and
have their password reset. Direct manipulation via the Django shell -- how the
sameer/imran accounts ended up in a broken state (superuser flags set,
passwords that did not match what was typed at the login screen, no role
assigned) -- bypasses validation, role assignment and the audit trail
entirely. Going through this module is what a User Management screen is for.
"""

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import transaction

from apps.accounts.models import Role, UserRole
from apps.audit.models import AuditAction
from common.services.audit import record_audit

User = get_user_model()

# Retain legacy organisation data for visibility rules and reporting, but do
# not expose it as writable input in User Management (including FK aliases).
REMOVED_PROFILE_FIELDS = frozenset({
    "designation", "department", "team", "site", "department_id", "team_id", "site_id",
})


def validate_user_profile_fields(fields):
    removed = REMOVED_PROFILE_FIELDS.intersection(fields)
    if removed:
        raise ValidationError({field: ["This field is no longer editable in User Management."]
                               for field in sorted(removed)})


@transaction.atomic
def create_user(*, actor, request=None, roles=None, password, **fields):
    """Create a user and assign their initial roles.

    A user created with no role sees an almost-empty app (every permission
    check fails), which is exactly the state imran and sameer were left in.
    Accepting `roles` here as part of creation, rather than as a follow-up
    step someone can forget, is deliberate.
    """
    validate_user_profile_fields(fields)
    user = User(**fields, created_by=getattr(actor, "unique_id", None),
               updated_by=getattr(actor, "unique_id", None))
    user.set_password(password)
    user.save()

    _sync_roles(user, roles or [], actor=actor)

    record_audit(action=AuditAction.USER_CREATED, entity=user, actor=actor,
                 new_value=user.username, request=request)
    return user


@transaction.atomic
def update_user(*, user, actor, request=None, roles=None, **fields):
    """Update profile fields and, if supplied, the role assignment.

    Password is never touched here -- see reset_password(). Mixing "edit the
    profile" and "change the password" into one call is how a password
    silently changes as a side effect of an unrelated edit, which is exactly
    the kind of surprise that produces support tickets like this one.
    """
    validate_user_profile_fields(fields)
    changes = {}
    for field, value in fields.items():
        if not hasattr(user, field):
            continue
        old = getattr(user, field)
        if old != value:
            changes[field] = (old, value)
            setattr(user, field, value)

    if changes:
        user.updated_by = getattr(actor, "unique_id", None)
        user.save()
        from common.services.audit import record_field_changes
        record_field_changes(entity=user, actor=actor, changes=changes,
                             action=AuditAction.USER_UPDATED, request=request)

    if roles is not None:
        _sync_roles(user, roles, actor=actor)
        record_audit(action=AuditAction.USER_ROLES_CHANGED, entity=user, actor=actor,
                     new_value=", ".join(sorted(roles)) or "(none)", request=request)

    return user


def _sync_roles(user, role_codes, *, actor):
    """Replace the user's active role set with exactly `role_codes`.

    A full replace rather than an add-only API: the User Management screen
    presents role assignment as "these are the roles this user holds", and an
    add-only API would make it impossible to remove a role through the UI.
    """
    wanted = set(role_codes)
    roles_by_code = {r.code: r for r in Role.objects.filter(code__in=wanted, is_active=True)}

    existing = {ur.role.code: ur for ur in UserRole.objects.filter(user=user).select_related("role")}

    for code, role in roles_by_code.items():
        if code in existing:
            if not existing[code].is_active:
                existing[code].is_active = True
                existing[code].save(update_fields=["is_active"])
        else:
            UserRole.objects.create(user=user, role=role, is_active=True,
                                    created_by=getattr(actor, "unique_id", None))

    for code, assignment in existing.items():
        if code not in wanted and assignment.is_active:
            assignment.is_active = False
            assignment.save(update_fields=["is_active"])


@transaction.atomic
def deactivate_user(*, user, actor, request=None):
    """Soft delete (spec 57). Also revokes every outstanding session token,
    so deactivating an account actually ends any session already in progress
    rather than merely blocking future logins.
    """
    from apps.authentication.services import revoke_all_user_tokens

    user.soft_delete(deleted_by=getattr(actor, "unique_id", None))
    revoke_all_user_tokens(user)

    record_audit(action=AuditAction.USER_DEACTIVATED, entity=user, actor=actor,
                 request=request)
    return user


@transaction.atomic
def reactivate_user(*, user, actor, request=None):
    user.restore(restored_by=getattr(actor, "unique_id", None))

    record_audit(action=AuditAction.USER_REACTIVATED, entity=user, actor=actor,
                 request=request)
    return user


@transaction.atomic
def reset_password(*, user, actor, new_password, require_change_at_login=True, request=None):
    """An admin setting someone else's password.

    Revokes every outstanding refresh token for the account, the same as a
    self-service password change (apps/authentication/views) -- a credential
    reset must end any session a departing or compromised account still holds
    open, not just block the next login attempt.
    """
    from apps.authentication.services import revoke_all_user_tokens
    from django.utils import timezone

    user.set_password(new_password)
    user.must_change_password = require_change_at_login
    user.last_password_change_at = timezone.now()
    user.updated_by = getattr(actor, "unique_id", None)
    user.save(update_fields=[
        "password", "must_change_password", "last_password_change_at",
        "updated_by", "updated_at",
    ])
    revoke_all_user_tokens(user)

    record_audit(action=AuditAction.USER_PASSWORD_RESET, entity=user, actor=actor,
                 request=request)
    return user
