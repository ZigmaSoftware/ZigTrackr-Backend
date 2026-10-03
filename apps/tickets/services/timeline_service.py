"""Merged activity timeline for a ticket.

A ticket's own history is thin -- TicketUpdate rows and nothing else. For a BUG
ticket the real narrative lives on the linked bug, which already records status
changes, assignments, testing and reopens in typed history tables. Reading both
is what makes the timeline the promised "assigned through closed" story rather
than a list of whatever anyone happened to type.
"""

from apps.accounts.services.role_labels import role_labels_for_users
from apps.tickets.constants import TicketUpdateSource


def _actor_name(user):
    if user is None:
        return "System"
    return user.display_name


def _with_actor_roles(events):
    ids = {event.get("_actor_user_uuid") for event in events if event.get("_actor_user_uuid")}
    labels = role_labels_for_users(ids, unique=True)
    for event in events:
        event["actor_role"] = labels.get(event.pop("_actor_user_uuid", None), "")
    return events


def build_ticket_timeline(ticket, *, public=False):
    activities = list(ticket.activities.select_related("actor_user").all())
    if activities:
        normalized = [{
            "type": row.event_type,
            "timestamp": row.occurred_at,
            "actor": row.actor_display_name if not public else (
                "You" if row.actor_type == "REQUESTER" else row.actor_display_name
            ),
            "_actor_user_uuid": str(row.actor_user.unique_id) if row.actor_type == "USER" and row.actor_user_id else None,
            "title": row.title,
            "description": row.public_description if public else row.description,
            "remarks": "",
        } for row in activities if not public or row.public_description]
        if public and not any(row.event_type == "TICKET_RECEIVED" for row in activities):
            normalized.insert(0, {
                "type": "TICKET_RECEIVED", "timestamp": ticket.created_at,
                "actor": "System", "title": "Ticket received",
                "description": f"Your request was received and registered as {ticket.reference}.",
                "remarks": "",
            })
            normalized.sort(key=lambda row: row["timestamp"])
        if not public and ticket.bug_id:
            from apps.bugs.constants import BugStatus
            from apps.bugs.services.timeline_service import build_timeline

            represented_statuses = {
                "TICKET_ASSIGNED": "ASSIGNED",
                "TICKET_REASSIGNED": "ASSIGNED",
                "WORK_STARTED": "IN_PROGRESS",
                "RETURNED_TO_DEVELOPER": "IN_PROGRESS",
                "TICKET_PENDING": "PENDING",
                "TICKET_ON_HOLD": "ON_HOLD",
                "TICKET_RECTIFIED": "TESTING",
                "TICKET_CLOSED": "CLOSED",
                "TICKET_REOPENED": "REOPENED",
                **{f"BUG_STATUS_{status}": status for status in BugStatus.values},
            }
            covered = {
                BugStatus(status).label for row in activities
                if (status := represented_statuses.get(row.event_type))
            }
            for row in build_timeline(ticket.bug):
                if row["type"] != "STATUS" or row.get("to_value") in covered:
                    continue
                normalized.append({
                    "type": "STATUS", "timestamp": row["timestamp"],
                    "actor": (row.get("actor") or {}).get("name") or "System",
                    "_actor_user_uuid": (row.get("actor") or {}).get("id"),
                    "title": row["description"], "description": row["description"],
                    "remarks": row.get("remarks") or "",
                })
            normalized.sort(key=lambda row: row["timestamp"])
        return _with_actor_roles(normalized)

    if public:
        from apps.tickets.services.policy import effective_status

        events = [{
            "type": "TICKET_RECEIVED", "timestamp": ticket.created_at,
            "actor": "System", "title": "Ticket received",
            "description": f"Your request was received and registered as {ticket.reference}.",
            "remarks": "",
        }]
        if ticket.owner_id:
            events.append({
                "type": "TICKET_ASSIGNED", "timestamp": ticket.updated_at,
                "actor": "Support desk", "title": "Ticket Assigned",
                "description": "Your request was assigned to a support specialist.",
                "remarks": "",
            })
        if effective_status(ticket) == "CLOSED":
            events.append({
                "type": "TICKET_CLOSED", "timestamp": ticket.updated_at,
                "actor": "Support desk", "title": "Ticket closed",
                "description": "Your request was completed and closed.",
                "remarks": "",
            })
        return _with_actor_roles(events)

    events = []

    for row in ticket.updates.all().select_related("created_by_user"):
        events.append({
            "type": "SYSTEM" if row.source == TicketUpdateSource.SYSTEM else "UPDATE",
            "timestamp": row.created_at,
            "actor": _actor_name(row.created_by_user),
            "_actor_user_uuid": str(row.created_by_user.unique_id) if row.created_by_user_id else None,
            "description": row.update_text,
            "remarks": row.remarks,
        })

    # The bug's typed history, folded in so a BUG ticket shows the work done on
    # it rather than an empty tab.
    if ticket.bug_id:
        from apps.bugs.services.timeline_service import build_timeline

        for row in build_timeline(ticket.bug):
            events.append({
                "type": row["type"],
                "timestamp": row["timestamp"],
                "actor": (row.get("actor") or {}).get("name") or "System",
                "_actor_user_uuid": (row.get("actor") or {}).get("id"),
                "description": row["description"],
                "remarks": row.get("remarks") or "",
            })

    # Oldest first: the tab reads forwards, like a story.
    events.sort(key=lambda event: event["timestamp"])
    return _with_actor_roles(events)
