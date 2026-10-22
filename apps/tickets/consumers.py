"""Authenticated WebSocket room shared by staff and the verified requester."""

from http.cookies import SimpleCookie
from types import SimpleNamespace

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer
from django.conf import settings
from django.http import Http404
from rest_framework_simplejwt.authentication import JWTAuthentication

from apps.tickets.models import SupportTicket
from apps.tickets.services.chat_service import send_message
from apps.tickets.services.public_track_service import verified_ticket
from common.exceptions.domain import WorkflowValidationError
from common.permissions.scoping import can_view_ticket
from common.authentication.session import validate_session


class TicketChatConsumer(AsyncJsonWebsocketConsumer):
    async def connect(self):
        self.cookies = self._cookies()
        self.public = self.scope["path"].endswith("/public/track/ws/")
        self.ticket_id = await self._authorize()
        if self.ticket_id is None:
            await self.close(code=4403)
            return
        self.room = f"ticket_{self.ticket_id}"
        await self.channel_layer.group_add(self.room, self.channel_name)
        await self.accept()

    async def disconnect(self, close_code):
        if hasattr(self, "room"):
            await self.channel_layer.group_discard(self.room, self.channel_name)

    async def receive_json(self, content, **kwargs):
        if not isinstance(content, dict) or content.get("type") != "send":
            await self.send_json({"type": "error", "message": "Unsupported action."})
            return
        if await self._authorize() != self.ticket_id:
            await self.close(code=4403)
            return
        value = content.get("message")
        if not isinstance(value, str):
            await self.send_json({"type": "error", "message": "Enter a message."})
            return
        error = await self._save(value)
        if error:
            await self.send_json({"type": "error", "message": error})

    async def ticket_message(self, event):
        if await self._authorize() != self.ticket_id:
            await self.close(code=4403)
            return
        await self.send_json({"type": "message", "message": event["message"]})

    async def ticket_changed(self, event):
        if await self._authorize() != self.ticket_id:
            await self.close(code=4403)
            return
        await self.send_json({"type": "message_changed", "message": event["message"]})

    async def ticket_receipts(self, event):
        if await self._authorize() != self.ticket_id:
            await self.close(code=4403)
            return
        await self.send_json({"type": "receipts_changed"})

    def _cookies(self):
        header = dict(self.scope.get("headers", [])).get(b"cookie", b"").decode("latin1")
        parsed = SimpleCookie()
        parsed.load(header)
        return {key: value.value for key, value in parsed.items()}

    @database_sync_to_async
    def _authorize(self):
        if self.public:
            # The session is scoped to an address, not one ticket, so the
            # socket says which ticket it wants. verified_ticket re-checks
            # that the signed-in address owns it, making the query string
            # untrusted input rather than a grant.
            from urllib.parse import parse_qs

            query = parse_qs(self.scope.get("query_string", b"").decode("latin1"))
            ticket_no = (query.get("ticket_no") or [""])[0]
            try:
                return verified_ticket(
                    SimpleNamespace(COOKIES=self.cookies), ticket_no,
                ).pk
            except Http404:
                return None
        token = self.cookies.get(settings.AUTH_COOKIE_NAME)
        if not token:
            return None
        try:
            auth = JWTAuthentication()
            validated_token = auth.get_validated_token(token)
            user = auth.get_user(validated_token)
            validate_session(validated_token, user)
            from common.permissions.company import can_access_company
            if not user.is_active or getattr(user, "is_deleted", False) or not can_access_company(user, user):
                return None
            ticket = SupportTicket.objects.select_related("bug", "owner").get(
                unique_id=self.scope["url_route"]["kwargs"]["unique_id"], is_deleted=False,
            )
            if not can_view_ticket(user, ticket):
                return None
            return ticket.pk
        except Exception:
            return None

    @database_sync_to_async
    def _save(self, value):
        ticket = SupportTicket.objects.select_related("bug", "owner").get(pk=self.ticket_id)
        try:
            if self.public:
                send_message(
                    ticket=ticket, text=value, requester_email=ticket.reported_by_email,
                )
            else:
                auth = JWTAuthentication()
                validated_token = auth.get_validated_token(
                    self.cookies[settings.AUTH_COOKIE_NAME],
                )
                user = auth.get_user(validated_token)
                validate_session(validated_token, user)
                send_message(ticket=ticket, text=value, user=user)
        except WorkflowValidationError as exc:
            return next(iter(exc.errors.values()))[0]
        return None


class UserInboxConsumer(AsyncJsonWebsocketConsumer):
    """Per-user nudge channel for unread counts.

    The ticket room only reaches people who have that conversation open, so a
    message on any other ticket went unseen until a poll fired. This gives each
    signed-in user a socket of their own.

    It deliberately carries NO content -- only {"type": "inbox_changed"}. Who
    may see a ticket is decided per user by can_view_ticket, which a broadcast
    group cannot evaluate, so sending the message itself here would risk
    leaking it to staff outside the ticket's scope. The client re-fetches its
    already-scoped inbox and badge queries instead, and the server answers
    those with the caller's own permissions applied.
    """

    async def connect(self):
        self.cookies = self._cookies()
        self.user_id = await self._authorize()
        if self.user_id is None:
            await self.close(code=4403)
            return
        self.room = f"user_inbox_{self.user_id}"
        await self.channel_layer.group_add(self.room, self.channel_name)
        await self.accept()

    async def disconnect(self, close_code):
        if hasattr(self, "room"):
            await self.channel_layer.group_discard(self.room, self.channel_name)

    async def receive_json(self, content, **kwargs):
        # Read-only channel: nothing a client sends here is honoured.
        await self.send_json({"type": "error", "message": "Unsupported action."})

    async def inbox_changed(self, event):
        # Re-checked on every event so a deactivated session stops receiving.
        if await self._authorize() != self.user_id:
            await self.close(code=4403)
            return
        await self.send_json({"type": "inbox_changed"})

    async def inbox_permissions_changed(self, event):
        if await self._authorize() != self.user_id:
            await self.close(code=4403)
            return
        await self.send_json({"type": "permissions_changed"})

    async def inbox_notification(self, event):
        """Deliver one notification, payload and all.

        `inbox_changed` above is deliberately contentless because ticket
        visibility is per user and a shared group cannot evaluate it. This
        frame is different: the row is already addressed to a single
        recipient and this group is that recipient's own channel, so the
        payload discloses nothing they could not fetch themselves.
        """
        if await self._authorize() != self.user_id:
            await self.close(code=4403)
            return
        await self.send_json({
            "type": "notification", "notification": event["notification"],
        })

    def _cookies(self):
        header = dict(self.scope.get("headers", [])).get(b"cookie", b"").decode("latin1")
        parsed = SimpleCookie()
        parsed.load(header)
        return {key: value.value for key, value in parsed.items()}

    @database_sync_to_async
    def _authorize(self):
        token = self.cookies.get(settings.AUTH_COOKIE_NAME)
        if not token:
            return None
        try:
            auth = JWTAuthentication()
            validated_token = auth.get_validated_token(token)
            user = auth.get_user(validated_token)
            validate_session(validated_token, user)
            from common.permissions.company import can_access_company
            if not user.is_active or getattr(user, "is_deleted", False) or not can_access_company(user, user):
                return None
            return user.pk
        except Exception:
            return None
