"""Login audit trail."""

import uuid

from django.conf import settings
from django.db import models


class LoginAuditLog(models.Model):
    id = models.BigAutoField(primary_key=True)
    unique_id = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="login_audits", db_column="user_id",
    )
    # Kept even when the user lookup fails, so failed attempts against unknown
    # usernames are still investigable.
    attempted_username = models.CharField(max_length=150, blank=True, default="")
    was_successful = models.BooleanField(default=False)
    failure_reason = models.CharField(max_length=100, blank=True, default="")
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=500, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "login_audit_log"
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["user", "-created_at"], name="idx_loginaudit_user"),
            models.Index(fields=["was_successful", "-created_at"], name="idx_loginaudit_success"),
        ]
