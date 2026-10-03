"""ASGI entry point for HTTP and ticket chat WebSockets."""

import os

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

from channels.routing import ProtocolTypeRouter, URLRouter
from channels.security.websocket import AllowedHostsOriginValidator
from django.core.asgi import get_asgi_application
from django.urls import re_path

django_application = get_asgi_application()

from apps.tickets.consumers import TicketChatConsumer

application = ProtocolTypeRouter({
    "http": django_application,
    "websocket": AllowedHostsOriginValidator(URLRouter([
        re_path(r"^api/v1/tickets/public/track/ws/$", TicketChatConsumer.as_asgi()),
        re_path(r"^api/v1/tickets/(?P<unique_id>[0-9a-fA-F-]{36})/ws/$",
                TicketChatConsumer.as_asgi()),
    ])),
})
