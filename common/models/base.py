"""Abstract base models.

Dual identity is the house convention: the integer `id` is the internal primary
key (compact, good for MariaDB indexing and joins), while `unique_id` is the
UUID exposed through the API. Viewsets use `lookup_field = "unique_id"` so
sequential integer ids are never guessable from a URL.
"""

import uuid

from django.db import models
from django.utils import timezone

from common.exceptions.domain import SystemRowProtectedError


class BaseMaster(models.Model):
    unique_id = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    is_active = models.BooleanField(default=True)
    is_deleted = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    deleted_at = models.DateTimeField(null=True, blank=True)
    created_by = models.UUIDField(null=True, blank=True)
    updated_by = models.UUIDField(null=True, blank=True)
    deleted_by = models.UUIDField(null=True, blank=True)

    class Meta:
        abstract = True

    def soft_delete(self, deleted_by=None):
        self.is_deleted = True
        self.is_active = False
        self.deleted_at = timezone.now()
        self.deleted_by = deleted_by
        if deleted_by is not None:
            self.updated_by = deleted_by
        self.save(update_fields=[
            "is_deleted", "is_active", "deleted_at",
            "deleted_by", "updated_by", "updated_at",
        ])

    def restore(self, restored_by=None):
        self.is_deleted = False
        self.is_active = True
        self.deleted_at = None
        self.deleted_by = None
        if restored_by is not None:
            self.updated_by = restored_by
        self.save(update_fields=[
            "is_deleted", "is_active", "deleted_at",
            "deleted_by", "updated_by", "updated_at",
        ])

    def delete(self, using=None, keep_parents=False, deleted_by=None):
        """Soft delete by default (spec 57). Use hard_delete() to really remove."""
        self.soft_delete(deleted_by=deleted_by)

    def hard_delete(self, using=None, keep_parents=False):
        return super().delete(using=using, keep_parents=keep_parents)


class NamedMaster(BaseMaster):
    name = models.CharField(max_length=120)
    description = models.TextField(blank=True, default="")

    class Meta:
        abstract = True
        ordering = ["name"]

    def __str__(self):
        return self.name


class CodedMaster(NamedMaster):
    """NamedMaster plus a stable machine code, display rank, colour and a system lock.

    Used by Priority, Severity and RootCauseType: masters the user may edit
    (spec 16) whose *meaning* is referenced by dashboard and report code.
    Application logic always filters on `code`; `name` is free text a user may
    rename at will. Seeded rows carry is_system=True and cannot be recoded or
    deleted, so `code` remains a dependable key.
    """

    code = models.CharField(max_length=30)
    rank = models.PositiveSmallIntegerField(default=100)
    color = models.CharField(max_length=9, blank=True, default="")
    is_system = models.BooleanField(default=False)

    class Meta:
        abstract = True
        ordering = ["rank", "name"]

    def soft_delete(self, deleted_by=None):
        # Enforced here, not only in the serializer: a serializer-only guard
        # protects one API path and nothing else -- a future bulk-delete
        # command, a management shell, or a new endpoint would silently
        # violate the "system rows cannot be deleted" contract this class
        # documents. Putting it on the model makes it true everywhere.
        if self.is_system:
            raise SystemRowProtectedError(
                f"{self._meta.verbose_name} '{self.code}' is a system row and cannot be deleted."
            )
        super().soft_delete(deleted_by=deleted_by)
