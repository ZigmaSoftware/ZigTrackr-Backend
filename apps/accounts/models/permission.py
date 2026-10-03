"""Permission model, materialised from the declarative catalog."""

from django.db import models

from common.models import BaseMaster


class Permission(BaseMaster):
    codename = models.CharField(max_length=120)
    name = models.CharField(max_length=160)
    module = models.CharField(max_length=60)
    screen_code = models.CharField(max_length=120, blank=True, default="")
    screen_name = models.CharField(max_length=160, blank=True, default="")
    action = models.CharField(max_length=20, blank=True, default="")
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = "permissions"
        ordering = ["sort_order", "module", "screen_code", "codename"]
        constraints = [
            models.UniqueConstraint(fields=["codename"], name="uq_permissions_codename"),
        ]
        indexes = [
            models.Index(fields=["module", "screen_code"], name="idx_perm_module_screen"),
            models.Index(fields=["is_active"], name="idx_perm_active"),
        ]

    def __str__(self):
        return self.codename
