"""Immutable history base (spec 10, 65).

Spec 65 is the core architectural principle of this system: never overwrite
history. Making that a base class rather than a code-review convention means a
future contributor cannot quietly break it -- an UPDATE or DELETE against any
history table raises instead of silently rewriting the audit record.
"""

import uuid

from django.db import models

from common.exceptions.domain import ImmutableRecordError


class ImmutableHistoryModel(models.Model):
    unique_id = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise ImmutableRecordError(
                f"{type(self).__name__} rows are immutable and cannot be modified."
            )
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ImmutableRecordError(
            f"{type(self).__name__} rows are immutable and cannot be deleted."
        )
