"""Role model."""

from django.db import models

from common.models import NamedMaster


class Role(NamedMaster):
    code = models.CharField(max_length=40)
    is_system = models.BooleanField(default=False)
    rank = models.PositiveSmallIntegerField(default=100)

    class Meta:
        db_table = "roles"
        ordering = ["rank", "name"]
        constraints = [
            models.UniqueConstraint(fields=["code"], name="uq_roles_code"),
        ]
        indexes = [
            models.Index(fields=["is_active", "is_deleted"], name="idx_roles_active"),
        ]

    def __str__(self):
        return self.name
