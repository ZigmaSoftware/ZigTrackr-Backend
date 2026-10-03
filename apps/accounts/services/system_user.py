"""The Mail Intake system user.

Email arrives from people who mostly have no account here, but `Bug.reported_by`
and `SupportTicket.reported_by` are non-nullable PROTECT foreign keys. Something
real has to own those rows, so intake attributes them to one dedicated account
and keeps the true sender address on the ticket instead.

Why a get_or_create helper rather than a data migration: a migration runs once
and then the row's existence depends on migration history, so a rename or a
deactivation leaves nothing to repair it, and every test database would need the
migration to have run in the right order relative to AUTH_USER_MODEL. A lazy
helper is self-healing and behaves identically in tests with no fixture at all.
This is Django's own get_sentinel_user pattern.
"""

from django.conf import settings
from django.contrib.auth import get_user_model

SYSTEM_USER_FULL_NAME = "Mail Intake (System)"


def get_mail_intake_user():
    """Return the non-loginable account that owns email-originated records.

    Deliberately not cached. An lru_cache would hold a stale ORM instance across
    a rolled-back test database, and one indexed lookup per message is nothing
    beside the IMAP round-trip that preceded it.
    """
    User = get_user_model()
    username = getattr(settings, "MAIL_INTAKE_SYSTEM_USERNAME", "mail.intake")

    user, created = User.objects.get_or_create(
        username=username,
        defaults={
            "full_name": SYSTEM_USER_FULL_NAME,
            # Left blank on purpose. AbstractUser.email is not unique here and
            # UsernameOrEmailBackend permits logging in by email, so giving this
            # account the intake address would collide with any real member of
            # staff who shares it.
            "email": "",
            # Active on purpose, counter-intuitive as it looks. User.soft_delete()
            # deactivates AND nulls employee_code, and tickets hold a PROTECT FK
            # to this row -- deactivating it would strand them. The security
            # property comes from the unusable password below, not from is_active.
            "is_active": True,
            "is_staff": False,
            "is_superuser": False,
        },
    )

    if created:
        # check_password() returns False for ANY input against an unusable
        # password, and UsernameOrEmailBackend routes through check_password,
        # so this account cannot authenticate by username or by email.
        user.set_unusable_password()
        user.save(update_fields=["password"])

    return user


def is_system_user(user):
    """True when `user` is the intake account rather than a person."""
    if user is None:
        return False
    username = getattr(settings, "MAIL_INTAKE_SYSTEM_USERNAME", "mail.intake")
    return getattr(user, "username", None) == username
