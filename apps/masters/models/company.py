from django.db import models

from common.models import NamedMaster


class CompanyMaster(NamedMaster):
    code = models.CharField(max_length=20, unique=True)
    support_email = models.EmailField(null=True, blank=True, unique=True)

    class Meta:
        db_table = "company_master"
        ordering = ["name"]
