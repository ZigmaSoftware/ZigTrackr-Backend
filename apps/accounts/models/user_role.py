"""User <-> Role assignment."""

import uuid

from django.conf import settings
from django.db import models


class UserRole(models.Model):
    id = models.BigAutoField(primary_key=True)
    unique_id = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name="user_roles", db_column="user_id",
    )
    role = models.ForeignKey(
        "accounts.Role", on_delete=models.PROTECT,
        related_name="user_roles", db_column="role_id",
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.UUIDField(null=True, blank=True)

    class Meta:
        db_table = "user_roles"
        constraints = [
            models.UniqueConstraint(fields=["user", "role"], name="uq_user_role"),
        ]
        indexes = [
            models.Index(fields=["user", "is_active"], name="idx_user_role_active"),
        ]

    def __str__(self):
        return f"{self.user_id}:{self.role_id}"
