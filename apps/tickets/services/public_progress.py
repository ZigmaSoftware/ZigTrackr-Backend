"""Requester milestones, independent of internal notes and paused work states."""

LABELS = {
    "ASSIGNED": "Assigned", "STARTED": "Started", "IN_PROGRESS": "In Progress",
    "RECTIFIED": "Rectified", "TESTING": "Testing", "RESOLVED": "Resolved",
    "RESENT": "Resent", "REOPEN": "Reopen", "CLOSED": "Closed",
    "REJECTED": "Closed",
}
DEVELOPMENT = ["STARTED", "IN_PROGRESS", "RECTIFIED", "TESTING", "RESOLVED"]
EVENT_STATUSES = {
    "TICKET_ASSIGNED": "ASSIGNED", "TICKET_REASSIGNED": "ASSIGNED",
    "WORK_STARTED": "IN_PROGRESS", "RETURNED_TO_DEVELOPER": "IN_PROGRESS",
    "TICKET_RETURNED_TO_DEVELOPER": "ASSIGNED",
    "TICKET_RECTIFIED": "TESTING", "TICKET_CLOSED": "CLOSED",
    "SERVICE_COMPLETED": "COMPLETED", "TICKET_REOPENED": "REOPENED",
    "TICKET_PENDING": "PENDING", "TICKET_ON_HOLD": "ON_HOLD",
}


def public_stage(status, *, awaiting_confirmation=False):
    if status == "CLOSED":
        return "RESOLVED" if awaiting_confirmation else "CLOSED"
    if status in ("RESOLVED", "COMPLETED"):
        return "RESOLVED"
    if status in ("PENDING", "ON_HOLD"):
        return "IN_PROGRESS"
    if status == "REOPENED":
        return "REOPEN"
    if status in ("TESTING", "REJECTED", "IN_PROGRESS"):
        return status
    return "ASSIGNED"


def progress_steps(transitions, status):
    """Build the current path using typed transitions; never inspect free text."""
    if status == "REJECTED":
        return [{"code": "CLOSED", "label": "Closed", "state": "complete", "completed_at": None}]
    codes = ["ASSIGNED", *DEVELOPMENT]
    dates = {}
    cursor = 0
    previous = None
    reopened = False

    for before, after, at in [*transitions, (None, status, None)]:
        if after == previous:
            continue
        before = before or previous
        if after == "REOPENED":
            codes = ["REOPEN", "TESTING", "RESOLVED"]
            dates = {0: at}
            cursor = 1
            reopened = True
        elif after in ("ASSIGNED", "IN_PROGRESS") and before in ("TESTING", "REOPENED"):
            if reopened:
                # Preserve the initial recheck and each further failed test.
                testing = max(i for i, code in enumerate(codes) if code == "TESTING")
                codes = [*codes[:testing + 1], "RESENT", *DEVELOPMENT]
                dates = {i: value for i, value in dates.items() if i <= testing}
                cursor = testing + 1
            else:
                codes = ["RESENT", *DEVELOPMENT]
                dates = {}
                cursor = 0
            dates[cursor] = at
            if after == "IN_PROGRESS":
                cursor += 2
                dates[cursor - 1] = dates[cursor] = at
        elif after in ("IN_PROGRESS", "PENDING", "ON_HOLD"):
            indices = [i for i, code in enumerate(codes) if code == "IN_PROGRESS"]
            if indices:
                cursor = indices[-1]
                if after == "IN_PROGRESS" and at:
                    dates.setdefault(cursor - 1, at)
                    dates.setdefault(cursor, at)
        elif after == "TESTING":
            cursor = max(i for i, code in enumerate(codes) if code == "TESTING")
            if at:
                dates[cursor] = at
                if cursor and codes[cursor - 1] == "RECTIFIED":
                    dates[cursor - 1] = at
        elif after in ("CLOSED", "RESOLVED", "COMPLETED", "REJECTED"):
            cursor = len(codes)
            if at:
                dates[len(codes) - 1] = at
        elif after == "ASSIGNED" and at:
            dates.setdefault(0, at)
        previous = after

    return [{
        "code": code,
        "label": LABELS[code],
        "state": "complete" if i < cursor else "current" if i == cursor else "upcoming",
        "completed_at": dates.get(i) if i < cursor else None,
    } for i, code in enumerate(codes)]


def ticket_progress(ticket):
    from apps.tickets.services.policy import effective_status

    transitions = []
    if ticket.bug_id:
        transitions = list(ticket.bug.status_history.order_by("changed_at", "id").values_list(
            "from_status", "to_status", "changed_at",
        ))
    if not transitions:
        for row in ticket.activities.order_by("occurred_at", "id").only("event_type", "occurred_at"):
            after = (row.event_type.removeprefix("BUG_STATUS_")
                     if row.event_type.startswith("BUG_STATUS_") else EVENT_STATUSES.get(row.event_type))
            if after:
                before = "TESTING" if row.event_type in (
                    "RETURNED_TO_DEVELOPER", "TICKET_RETURNED_TO_DEVELOPER",
                ) else None
                transitions.append((before, after, row.occurred_at))
    return progress_steps(transitions, effective_status(ticket))
