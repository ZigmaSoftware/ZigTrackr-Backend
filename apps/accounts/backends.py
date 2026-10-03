"""Authenticate by exact-case username or email, even on CI DB collations."""

from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend
from django.db.models import Q


class UsernameOrEmailBackend(ModelBackend):
    def authenticate(self, request, username=None, password=None, **kwargs):
        User = get_user_model()
        if username is None or password is None:
            return None

        # MariaDB's utf8mb4_unicode_ci treats __exact as case-insensitive.
        # Narrow with the indexed columns, then compare Python strings exactly.
        candidates = list(User.objects.filter(
            Q(username__iexact=username) | Q(email__iexact=username),
            is_deleted=False,
        ))
        matches = [user for user in candidates if user.username == username or user.email == username]
        if len(matches) != 1:
            # Preserve the timing work for missing and ambiguous identifiers.
            User().set_password(password)
            return None

        user = matches[0]
        if user.check_password(password) and self.user_can_authenticate(user):
            return user
        return None
