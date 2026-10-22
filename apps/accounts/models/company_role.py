"""Company-specific role settings; absent rows inherit the global role."""

from django.db import models

from common.models import BaseMaster


class CompanyRole(BaseMaster):
    company = models.ForeignKey("masters.CompanyMaster", on_delete=models.PROTECT, related_name="role_settings")
    role = models.ForeignKey("accounts.Role", on_delete=models.PROTECT, related_name="company_settings")
    name = models.CharField(max_length=120)
    description = models.TextField(blank=True, default="")
    permissions = models.ManyToManyField("accounts.Permission", db_table="company_role_permissions", blank=True)

    class Meta:
        db_table = "company_roles"
        constraints = [models.UniqueConstraint(fields=["company", "role"], name="uq_company_role")]

    def __str__(self):
        return f"{self.company_id}:{self.role.code}"
