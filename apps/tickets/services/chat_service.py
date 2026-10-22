"""One persisted conversation for internal staff and the verified requester."""

import logging
import uuid

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied

from apps.accounts.services.role_labels import role_labels_for_users
from apps.tickets.models import TicketChatMessage, TicketChatRevision
from apps.tickets.services.policy import chat_state
from common.exceptions.domain import WorkflowValidationError

logger = logging.getLogger(__name__)


REACTION_CHOICES = {"👍", "❤️", "😂", "😮", "😢", "🙏"}


def actor_key(*, user=None, requester_email=""):
    return "requester" if requester_email else f"staff:{user.pk}"


def message_uuid(value):
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError, AttributeError):
        raise WorkflowValidationError({"detail": ["Invalid message ID."]}) from None


def message_role_labels(messages):
    """Batch role lookup for a chat page, including quoted-message authors."""
    user_ids = set()
    for message in messages:
        if message.sender_type == "STAFF" and message.sender_user_id:
            user_ids.add(message.sender_user_id)
        reply = message.reply_to_message
        if reply and reply.sender_type == "STAFF" and reply.sender_user_id:
            user_ids.add(reply.sender_user_id)
    return role_labels_for_users(user_ids)


def serialize_message(message, *, user=None, requester_email="", role_labels=None):
    actor = actor_key(user=user, requester_email=requester_email) if user or requester_email else None
    reactions = message.reactions or {}
    reply = message.reply_to_message
    if role_labels is None:
        role_labels = message_role_labels([message])
    return {
        "id": str(message.unique_id),
        "sender_type": message.sender_type,
        "sender_display_name": message.sender_display_name,
        "sender_role": role_labels.get(message.sender_user_id, "") if message.sender_type == "STAFF" else "",
        "sender_email": message.sender_email if message.sender_type == "REQUESTER" else "",
        "message_text": "" if message.is_deleted else message.message_text,
        "created_at": message.created_at.isoformat(),
        "delivered_at": message.delivered_at.isoformat() if message.delivered_at else None,
        "read_at": message.read_at.isoformat() if message.read_at else None,
        "is_mine": bool(actor and (
            message.sender_type == "REQUESTER" and actor == "requester"
            or message.sender_type == "STAFF" and user and message.sender_user_id == user.pk
        )),
        "edited_at": message.edited_at.isoformat() if message.edited_at else None,
        "is_deleted": message.is_deleted,
        "is_pinned": bool(message.pinned_at),
        "is_starred": actor in (message.starred_by or []) if actor else False,
        "can_edit": bool(actor and not message.is_deleted and not message.read_at
                         and not message.is_system_message and (
            message.sender_type == "REQUESTER" and actor == "requester"
            or message.sender_type == "STAFF" and user and message.sender_user_id == user.pk
        )),
        "reactions": [{"emoji": emoji, "count": len(actors), "mine": actor in actors if actor else False}
                      for emoji, actors in reactions.items() if actors],
        "reply_to": {"id": str(reply.unique_id), "sender_display_name": reply.sender_display_name,
                     "sender_role": role_labels.get(reply.sender_user_id, "") if reply.sender_type == "STAFF" else "",
                     "message_text": "" if reply.is_deleted else reply.message_text,
                     "is_deleted": reply.is_deleted} if reply else None,
    }


@transaction.atomic
def send_message(*, ticket, text, user=None, requester_email="", reply_to_id=None):
    state = chat_state(ticket, user=user, requester_email=requester_email)
    if not state["can_send"]:
        raise WorkflowValidationError({"detail": [state["reason"]]})
    text = (text or "").strip()
    if not text or len(text) > 4000:
        raise WorkflowValidationError({"message": ["Message must be between 1 and 4000 characters."]})
    reply = None
    if reply_to_id:
        reply = TicketChatMessage.objects.filter(ticket=ticket, unique_id=message_uuid(reply_to_id)).first()
        if not reply or reply.is_deleted:
            raise WorkflowValidationError({"reply_to": ["That message is no longer available."]})
    row = TicketChatMessage.objects.create(
        ticket=ticket,
        sender_type="REQUESTER" if requester_email else "STAFF",
        sender_user=user,
        sender_email=requester_email,
        sender_display_name=(ticket.reported_by_name or requester_email) if requester_email
        else user.display_name,
        message_text=text,
        reply_to_message=reply,
    )
    publish_message(row)
    _notify_chat_message(row, ticket, actor=user)
    return row


def _notify_chat_message(row, ticket, *, actor):
    """Tell the counterpart, not the whole team.

    Deliberately narrower than notify_inbox_changed's fan-out: that nudge
    only refreshes unread counts, so casting wide is harmless. A chat
    notification is an interruption, and messages are the highest-volume
    event here -- sending one to every admin on every reply is how people
    end up muting notifications entirely.

    A requester's message notifies the ticket's owner. A staff message
    notifies nobody in-app: the requester is not a system user, and they
    already receive it over the public track socket.
    """
    from apps.notifications.models import NotificationType
    from apps.notifications.services import notify

    if row.sender_type != "REQUESTER" or not ticket.owner_id:
        return
    notify(
        recipients=[ticket.owner_id],
        notification_type=NotificationType.CHAT_MESSAGE,
        title=f"New message on {ticket.reference}",
        message=row.message_text[:160],
        ticket=ticket,
        link=f"/chat?ticket={ticket.unique_id}",
        actor=actor,
    )


def notify_inbox_changed(ticket):
    """Nudge everyone whose unread counts this ticket could affect.

    Sent per user rather than to one shared group because ticket visibility is
    evaluated per user (scope_ticket_queryset): a single broadcast group has no
    way to apply that. The nudge carries no content for the same reason -- the
    client re-fetches its own scoped queries, so a recipient who cannot see the
    ticket simply learns nothing from it.
    """
    from common.permissions.require import user_ids_with_permission

    recipients = user_ids_with_permission(
        "tickets.ticket.view_all", "tickets.ticket.classify",
    )
    # The people the ticket is actually about, who may hold neither codename.
    for attr in ("owner_id", "reported_by_id"):
        value = getattr(ticket, attr, None)
        if value:
            recipients.add(value)

    layer = get_channel_layer()
    from apps.accounts.models import User
    from common.permissions.company import can_access_company
    recipients = [person.pk for person in User.objects.filter(pk__in=recipients, is_active=True, is_deleted=False).select_related("company")
                  if can_access_company(person, ticket)]
    for user_id in recipients:
        try:
            async_to_sync(layer.group_send)(
                f"user_inbox_{user_id}", {"type": "inbox.changed"},
            )
        except Exception:
            logger.exception("Inbox nudge failed for user %s", user_id)


def publish_message(row, *, changed=False):
    def publish():
        try:
            async_to_sync(get_channel_layer().group_send)(
                f"ticket_{row.ticket_id}",
                {"type": "ticket.changed" if changed else "ticket.message",
                 "message": serialize_message(row)},
            )
        except Exception:
            logger.exception("Ticket chat broadcast failed; message %s is persisted", row.pk)
        # Separate from the room broadcast: the room only reaches people with
        # this conversation open, which is exactly the case that was missing.
        if not changed:
            notify_inbox_changed(row.ticket)

    transaction.on_commit(publish)


@transaction.atomic
def acknowledge_messages(*, ticket, message_ids, status, user=None, requester_email=""):
    if status not in ("delivered", "read"):
        raise WorkflowValidationError({"status": ["Unsupported receipt status."]})
    recipient_type = "REQUESTER" if requester_email else "STAFF"
    now = timezone.now()
    rows = list(TicketChatMessage.objects.select_for_update().select_related("reply_to_message").filter(
        ticket=ticket, unique_id__in=message_ids, is_deleted=False,
    ).exclude(sender_type=recipient_type))
    changed = []
    for row in rows:
        if row.delivered_at is None:
            row.delivered_at = now
            changed.append(row)
        if status == "read" and row.read_at is None:
            row.read_at = now
            if row not in changed:
                changed.append(row)
    if changed:
        TicketChatMessage.objects.bulk_update(changed, ["delivered_at", "read_at"])

        def publish():
            try:
                async_to_sync(get_channel_layer().group_send)(
                    f"ticket_{ticket.pk}", {"type": "ticket.receipts"},
                )
            except Exception:
                logger.exception("Ticket receipt broadcast failed; ticket %s is updated", ticket.pk)
            # Reading messages clears an unread badge, so the counts move here
            # too -- not just when a message arrives.
            notify_inbox_changed(ticket)

        transaction.on_commit(publish)
    role_labels = message_role_labels(changed)
    return [serialize_message(row, user=user, requester_email=requester_email,
                              role_labels=role_labels) for row in changed]


@transaction.atomic
def change_message(*, ticket, message_id, action, user=None, requester_email="", text=None, emoji=None):
    row = TicketChatMessage.objects.select_for_update().select_related("reply_to_message").filter(
        ticket=ticket, unique_id=message_uuid(message_id),
    ).first()
    if not row:
        raise WorkflowValidationError({"detail": ["Message not found."]})
    actor = actor_key(user=user, requester_email=requester_email)
    mine = row.sender_type == "REQUESTER" and actor == "requester" or (
        row.sender_type == "STAFF" and user and row.sender_user_id == user.pk
    )
    if action in ("edit", "delete") and (not mine or row.is_system_message):
        raise PermissionDenied("You can change only your own messages.")
    if action == "edit" and row.read_at:
        raise WorkflowValidationError({"message": ["This message has already been read and cannot be edited."]})
    if row.is_deleted and action != "star":
        raise WorkflowValidationError({"detail": ["This message was deleted."]})
    if action != "star":
        state = chat_state(ticket, user=user, requester_email=requester_email)
        if not state["can_send"]:
            raise WorkflowValidationError({"detail": [state["reason"]]})
    if action == "edit":
        value = (text or "").strip() if isinstance(text, str) else ""
        if not value or len(value) > 4000:
            raise WorkflowValidationError({"message": ["Message must be between 1 and 4000 characters."]})
        if value != row.message_text:
            TicketChatRevision.objects.create(message=row, previous_text=row.message_text)
            row.message_text = value
            row.edited_at = timezone.now()
            row.save(update_fields=["message_text", "edited_at"])
    elif action == "delete":
        row.is_deleted = True
        row.deleted_at = timezone.now()
        row.deleted_by = user
        row.save(update_fields=["is_deleted", "deleted_at", "deleted_by"])
    elif action == "react":
        if not isinstance(emoji, str) or emoji not in REACTION_CHOICES:
            raise WorkflowValidationError({"emoji": ["Unsupported reaction."]})
        reactions = {symbol: [key for key in keys if key != actor]
                     for symbol, keys in (row.reactions or {}).items()}
        if actor not in (row.reactions or {}).get(emoji, []):
            reactions.setdefault(emoji, []).append(actor)
        row.reactions = {symbol: keys for symbol, keys in reactions.items() if keys}
        row.save(update_fields=["reactions"])
    elif action == "star":
        starred = set(row.starred_by or [])
        if actor in starred:
            starred.remove(actor)
        else:
            starred.add(actor)
        row.starred_by = sorted(starred)
        row.save(update_fields=["starred_by"])
    elif action == "pin":
        row.pinned_at = None if row.pinned_at else timezone.now()
        row.save(update_fields=["pinned_at"])
    else:
        raise WorkflowValidationError({"detail": ["Unsupported chat action."]})
    if action != "star":
        publish_message(row, changed=True)
    return row
