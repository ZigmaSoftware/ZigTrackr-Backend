"""Bug creation and field updates."""

import datetime

from django.db import transaction

from apps.audit.models import AuditAction
from apps.bugs.constants import BugStatus
from apps.bugs.models import Bug, BugStatusHistory
from apps.bugs.services.bug_number import generate_bug_no
from common.services.audit import record_audit, record_field_changes
from common.utils.dates import local_today

# Field changes worth an individual audit row (spec 41).
AUDITED_FIELDS = {
    "priority": AuditAction.PRIORITY_CHANGE,
    "severity": AuditAction.SEVERITY_CHANGE,
    "expected_closure_date": AuditAction.EXPECTED_CLOSURE_CHANGE,
    "root_cause": AuditAction.ROOT_CAUSE_UPDATE,
    "resolution": AuditAction.RESOLUTION_UPDATE,
}


def suggest_expected_closure(priority, reported_date):
    """Default the expected closure date from the priority's SLA.

    Without a date, spec 12's overdue logic has nothing to measure, so a
    sensible default matters more than it looks.
    """
    sla_days = getattr(priority, "sla_days", None)
    if not sla_days:
        return None
    return reported_date + datetime.timedelta(days=sla_days)


@transaction.atomic
def create_bug(*, actor, request=None, **fields):
    """Create a bug with a generated number and an opening history row."""
    reported_date = fields.pop("reported_date", None) or local_today()

    bug = Bug(
        reported_date=reported_date,
        reported_by=fields.pop("reported_by", None) or actor,
        status=BugStatus.NEW,
        created_by=getattr(actor, "unique_id", None),
        updated_by=getattr(actor, "unique_id", None),
        **fields,
    )

    if bug.expected_closure_date is None:
        bug.expected_closure_date = suggest_expected_closure(bug.priority, reported_date)

    bug.bug_no = generate_bug_no(reported_date)
    bug.save()

    # Opening history row: from_status blank marks creation (spec 28 timeline).
    BugStatusHistory.objects.create(
        bug=bug, from_status="", to_status=BugStatus.NEW,
        remarks="Bug reported.", changed_by=actor,
    )
    record_audit(action=AuditAction.BUG_CREATED, entity=bug, actor=actor,
                 new_value=bug.bug_no, request=request)
    return bug


@transaction.atomic
def update_bug(*, bug, actor, request=None, **fields):
    """Update editable fields, auditing the significant ones.

    Status is deliberately not accepted here: it moves only through the
    workflow service, so that spec 29/30 cannot be bypassed by a field edit.
    """
    fields.pop("status", None)
    fields.pop("bug_no", None)

    changes = {}
    for field, value in fields.items():
        if not hasattr(bug, field):
            continue
        old = getattr(bug, field)
        if old != value:
            changes[field] = (old, value)
            setattr(bug, field, value)

    if not changes:
        return bug

    bug.updated_by = getattr(actor, "unique_id", None)
    bug.save()

    for field, action in AUDITED_FIELDS.items():
        if field in changes:
            old, new = changes[field]
            record_audit(action=action, entity=bug, actor=actor, field_name=field,
                         old_value=old, new_value=new, request=request)

    other = {f: v for f, v in changes.items() if f not in AUDITED_FIELDS}
    if other:
        record_field_changes(entity=bug, actor=actor, changes=other,
                             action=AuditAction.BUG_UPDATED, request=request)
    return bug
