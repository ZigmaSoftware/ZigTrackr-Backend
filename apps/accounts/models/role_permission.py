"""Role <-> Permission assignment."""

import uuid

from django.db import models


class RolePermission(models.Model):
    id = models.BigAutoField(primary_key=True)
    unique_id = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    role = models.ForeignKey(
        "accounts.Role", on_delete=models.CASCADE,
        related_name="role_permissions", db_column="role_id",
    )
    permission = models.ForeignKey(
        "accounts.Permission", on_delete=models.CASCADE,
        related_name="role_permissions", db_column="permission_id",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.UUIDField(null=True, blank=True)

    class Meta:
        db_table = "role_permissions"
        constraints = [
            models.UniqueConstraint(fields=["role", "permission"], name="uq_role_permission"),
        ]
        indexes = [
            models.Index(fields=["role"], name="idx_role_perm_role"),
        ]

    def __str__(self):
        return f"{self.role_id}:{self.permission_id}"
