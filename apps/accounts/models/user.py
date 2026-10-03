"""Custom user model."""

import uuid

from django.contrib.auth.models import AbstractUser
from django.db import models
from django.utils import timezone


class User(AbstractUser):
    """AbstractUser plus a public UUID and organisation fields.

    AbstractUser rather than AbstractBaseUser: it brings Django's password
    hashers, is_active, and the password change/reset machinery for free
    (spec 46), and it keeps `username` alongside `email` so the login screen can
    accept either (spec 20.3).

    `groups` and `user_permissions` are removed deliberately. Authorization is
    served by this app's Role/Permission tables (spec 16 requires a Permission
    Management screen, which Django's auto-generated auth_permission rows cannot
    meaningfully provide); leaving Django's M2Ms in place would create a second,
    competing answer to "what can this user do".
    """

    id = models.BigAutoField(primary_key=True)
    unique_id = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)

    groups = None
    user_permissions = None

    employee_code = models.CharField(max_length=30, null=True, blank=True)
    full_name = models.CharField(max_length=150, blank=True, default="")
    phone = models.CharField(max_length=20, blank=True, default="")
    designation = models.CharField(max_length=100, blank=True, default="")

    department = models.ForeignKey(
        "masters.DepartmentMaster", on_delete=models.PROTECT, null=True, blank=True,
        related_name="users", db_column="department_id",
    )
    team = models.ForeignKey(
        "masters.TeamMaster", on_delete=models.PROTECT, null=True, blank=True,
        related_name="members", db_column="team_id",
    )
    site = models.ForeignKey(
        "masters.SiteMaster", on_delete=models.PROTECT, null=True, blank=True,
        related_name="users", db_column="site_id",
    )

    # ---- SOFT DELETE (spec 57) ----
    is_deleted = models.BooleanField(default=False)
    deleted_at = models.DateTimeField(null=True, blank=True)
    deleted_by = models.UUIDField(null=True, blank=True)
    created_by = models.UUIDField(null=True, blank=True)
    updated_by = models.UUIDField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    last_password_change_at = models.DateTimeField(null=True, blank=True)
    must_change_password = models.BooleanField(default=False)

    class Meta:
        db_table = "users"
        ordering = ["full_name", "username"]
        constraints = [
            models.UniqueConstraint(fields=["employee_code"], name="uq_users_employee_code"),
        ]
        indexes = [
            models.Index(fields=["is_active", "is_deleted"], name="idx_users_active"),
            models.Index(fields=["team", "is_active"], name="idx_users_team_active"),
            models.Index(fields=["department"], name="idx_users_department"),
        ]

    def __str__(self):
        return self.full_name or self.username

    def save(self, *args, **kwargs):
        if not self.full_name:
            self.full_name = f"{self.first_name} {self.last_name}".strip() or self.username
        super().save(*args, **kwargs)

    def soft_delete(self, deleted_by=None):
        """Deactivate and release the employee code.

        MariaDB unique indexes ignore NULLs, so nulling employee_code frees it
        for reuse without needing a filtered unique index (which MariaDB does
        not support -- spec 2).
        """
        self.is_deleted = True
        self.is_active = False
        self.deleted_at = timezone.now()
        self.deleted_by = deleted_by
        self.employee_code = None
        self.save(update_fields=[
            "is_deleted", "is_active", "deleted_at", "deleted_by",
            "employee_code", "updated_at",
        ])

    def restore(self, restored_by=None):
        """Reactivate a deactivated account.

        Does not restore employee_code: soft_delete() nulled it to free the
        value for reuse (spec 2), so if it was reassigned to someone else in
        the meantime there is nothing to restore it to. An admin re-enters it
        explicitly via update_user() if it still applies.
        """
        self.is_deleted = False
        self.is_active = True
        self.deleted_at = None
        self.deleted_by = None
        if restored_by is not None:
            self.updated_by = restored_by
        self.save(update_fields=[
            "is_deleted", "is_active", "deleted_at", "deleted_by",
            "updated_by", "updated_at",
        ])

    @property
    def display_name(self):
        return self.full_name or self.username
