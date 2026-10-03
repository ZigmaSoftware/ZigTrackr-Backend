"""Stable, readable ticket events separate from technical audit records."""

from django.utils import timezone

from apps.tickets.models import TicketActivity


def record_activity(*, ticket, event_type, title, description="", public_description="",
                    actor=None, actor_email="", source_key=None, metadata=None):
    values = {
        "ticket": ticket,
        "event_type": event_type,
        "actor_type": "USER" if actor else ("REQUESTER" if actor_email else "SYSTEM"),
        "actor_user": actor,
        "actor_email": actor_email,
        "actor_display_name": getattr(actor, "display_name", "") or actor_email or "System",
        "title": title,
        "description": description,
        "public_description": public_description,
        "metadata": metadata or {},
        "occurred_at": timezone.now(),
        "source_key": source_key,
    }
    if source_key:
        return TicketActivity.objects.get_or_create(source_key=source_key, defaults=values)[0]
    return TicketActivity.objects.create(**values)
