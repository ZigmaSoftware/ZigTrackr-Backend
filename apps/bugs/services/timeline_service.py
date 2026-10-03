"""Merged activity timeline (spec 28).

Reads the typed history tables rather than audit_log: those are the
user-facing narrative, while audit_log is the flat compliance trail. A bug has
tens of events, not thousands, so five small queries merged in Python is the
right trade against a complex SQL union.
"""

from apps.bugs.constants import BugStatus


def _actor(user):
    if user is None:
        return None
    return {"id": str(user.unique_id), "name": user.display_name}


def build_timeline(bug):
    events = []

    for row in bug.status_history.all().select_related("changed_by"):
        if row.from_status:
            description = (f"Status changed from {BugStatus(row.from_status).label} "
                           f"to {BugStatus(row.to_status).label}")
        else:
            description = "Bug created"
        events.append({
            "type": "STATUS",
            "timestamp": row.changed_at,
            "actor": _actor(row.changed_by),
            "description": description,
            "from_value": BugStatus(row.from_status).label if row.from_status else None,
            "to_value": BugStatus(row.to_status).label,
            "remarks": row.remarks,
        })

    for row in bug.assignment_history.all().select_related(
            "from_owner", "to_owner", "assigned_by"):
        from_name = row.from_owner.display_name if row.from_owner else None
        to_name = row.to_owner.display_name if row.to_owner else "Unassigned"
        events.append({
            "type": "ASSIGNMENT",
            "timestamp": row.assigned_at,
            "actor": _actor(row.assigned_by),
            "description": (f"Reassigned from {from_name} to {to_name}"
                            if from_name else f"Assigned to {to_name}"),
            "from_value": from_name,
            "to_value": to_name,
            "remarks": row.remarks,
        })

    for row in bug.updates.all().select_related("updated_by"):
        if row.is_system_generated:
            # The status event already covers this; showing both duplicates the row.
            continue
        events.append({
            "type": "UPDATE",
            "timestamp": row.created_at,
            "actor": _actor(row.updated_by),
            "description": "Daily update added",
            "from_value": None,
            "to_value": None,
            "remarks": row.update_text,
            "next_action": row.next_action,
        })

    for row in bug.testing_history.all().select_related("tested_by"):
        events.append({
            "type": "TESTING",
            "timestamp": row.tested_at,
            "actor": _actor(row.tested_by),
            "description": f"Test {row.get_test_result_display().lower()}",
            "from_value": None,
            "to_value": row.get_test_result_display(),
            "remarks": row.test_remarks,
        })

    for row in bug.reopen_history.all().select_related("reopened_by"):
        events.append({
            "type": "REOPEN",
            "timestamp": row.reopened_at,
            "actor": _actor(row.reopened_by),
            "description": "Bug reopened",
            "from_value": None,
            "to_value": None,
            "remarks": row.reopen_reason,
        })

    for row in bug.attachments.filter(is_deleted=False).select_related("uploaded_by"):
        events.append({
            "type": "ATTACHMENT",
            "timestamp": row.uploaded_at,
            "actor": _actor(row.uploaded_by),
            "description": f"Attachment uploaded: {row.file_name}",
            "from_value": None,
            "to_value": row.file_name,
            "remarks": "",
        })

    events.sort(key=lambda e: e["timestamp"])
    return events
