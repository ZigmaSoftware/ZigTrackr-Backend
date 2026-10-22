"""The write path for in-app notifications.

The Notification model and its REST API existed long before anything called
them, so until now no notification was ever created. This module is the
missing link: one helper the service layer calls beside record_audit.

Three rules shape it:

  1. The actor never notifies themselves. You already know what you just did,
     and a self-notification is pure noise.
  2. Rows are created on commit, like publish_message. A rolled-back
     operation must not leave a notification describing something that never
     happened.
  3. It never raises. Telling someone about a change is strictly less
     important than the change itself, so a broadcast failure is logged and
     swallowed rather than rolling back a completed assignment.
"""

import logging

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.db import transaction

from apps.notifications.models import Notification

logger = logging.getLogger(__name__)


def _pk(value):
    """Accept either a user instance or a bare primary key."""
    return getattr(value, "pk", value)


def _broadcast(rows):
    """Push each row to its own recipient's channel.

    Unlike the contentless `inbox_changed` nudge, this carries the payload.
    That is safe precisely because the row is already addressed to one
    person and `user_inbox_<pk>` is that person's own group -- nothing is
    disclosed that they could not fetch from /notifications/ themselves.
    """
    from apps.notifications.serializers import NotificationSerializer

    layer = get_channel_layer()
    if layer is None:
        return
    for row in rows:
        try:
            async_to_sync(layer.group_send)(
                f"user_inbox_{row.recipient_id}",
                {"type": "inbox.notification",
                 "notification": NotificationSerializer(row).data},
            )
        except Exception:
            logger.exception(
                "Notification %s saved but could not be broadcast", row.pk,
            )


def notify(*, recipients, notification_type, title, message="",
           ticket=None, bug=None, link="", actor=None):
    """Create one notification per recipient and push it to them live.

    `recipients` may contain users, primary keys, duplicates and None --
    someone often qualifies by more than one route (owner *and* team lead),
    and they should still be told exactly once.

    Returns the created rows, or [] when there is no one left to tell.
    """
    actor_pk = _pk(actor) if actor is not None else None
    targets = {
        _pk(person) for person in (recipients or []) if person is not None
    }
    targets.discard(None)
    targets.discard(actor_pk)
    from apps.accounts.models import User
    from common.permissions.company import can_access_company
    entity = ticket or bug
    if entity is not None:
        targets = {user.pk for user in User.objects.filter(pk__in=targets, is_active=True, is_deleted=False).select_related("company")
                   if can_access_company(user, entity)}
    if not targets:
        return []

    rows = [
        Notification(
            recipient_id=target,
            notification_type=notification_type,
            title=title[:200],
            message=message or "",
            ticket=ticket,
            bug=bug,
            link=link[:300] if link else "",
            created_by_id=actor_pk,
        )
        for target in sorted(targets)
    ]

    try:
        created = Notification.objects.bulk_create(rows)
    except Exception:
        logger.exception("Could not create notifications (%s)", notification_type)
        return []

    # bulk_create does not populate PKs on every backend, so re-read what we
    # just wrote when the broadcast needs them.
    transaction.on_commit(lambda: _broadcast(created))
    return created
