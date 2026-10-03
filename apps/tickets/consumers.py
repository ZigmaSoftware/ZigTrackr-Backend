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
        await self.send_json({"type": "message", "message": event["message"]})

    async def ticket_changed(self, event):
        await self.send_json({"type": "message_changed", "message": event["message"]})

    async def ticket_receipts(self, event):
        await self.send_json({"type": "receipts_changed"})

    def _cookies(self):
        header = dict(self.scope.get("headers", [])).get(b"cookie", b"").decode("latin1")
        parsed = SimpleCookie()
        parsed.load(header)
        return {key: value.value for key, value in parsed.items()}

    @database_sync_to_async
    def _authorize(self):
        if self.public:
            try:
                return verified_ticket(SimpleNamespace(COOKIES=self.cookies)).pk
            except Http404:
                return None
        token = self.cookies.get(settings.AUTH_COOKIE_NAME)
        if not token:
            return None
        try:
            auth = JWTAuthentication()
            user = auth.get_user(auth.get_validated_token(token))
            if not user.is_active or getattr(user, "is_deleted", False):
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
                user = auth.get_user(auth.get_validated_token(
                    self.cookies[settings.AUTH_COOKIE_NAME],
                ))
                send_message(ticket=ticket, text=value, user=user)
        except WorkflowValidationError as exc:
            return next(iter(exc.errors.values()))[0]
        return None
