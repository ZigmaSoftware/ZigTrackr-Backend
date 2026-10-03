"""Resolve, close and reopen (spec 30, 39.7)."""

from django.db import transaction

from apps.audit.models import AuditAction
from apps.bugs.constants import BugStatus
from apps.bugs.models import BugReopenHistory
from apps.bugs.services.status_service import change_status
from common.services.audit import record_audit
from common.utils.dates import local_now, local_today


@transaction.atomic
def resolve_bug(*, bug, actor, root_cause, resolution, root_cause_type=None,
                resolved_date=None, remarks="", request=None,
                allow_reopened_verification=False):
    """Record the root cause and resolution, then move to Resolved."""
    resolved_date = resolved_date or local_today()

    bug.root_cause = root_cause
    bug.resolution = resolution
    bug.resolved_date = resolved_date
    bug.resolved_by = actor
    if root_cause_type is not None:
        bug.root_cause_type = root_cause_type
    bug.updated_by = getattr(actor, "unique_id", None)
    bug.save(update_fields=[
        "root_cause", "resolution", "resolved_date", "resolved_by",
        "root_cause_type", "updated_by", "updated_at",
    ])

    record_audit(action=AuditAction.RESOLVED, entity=bug, actor=actor,
                 new_value=resolution[:200], remarks=remarks, request=request)

    return change_status(
        bug=bug, to_status=BugStatus.RESOLVED, actor=actor,
        remarks=remarks or "Bug resolved.", request=request,
        allow_reopened_verification=allow_reopened_verification,
    )


@transaction.atomic
def close_bug(*, bug, actor, closure_remarks, verification_result=None,
              closed_date=None, remarks="", request=None):
    """Close a bug once every spec 30 requirement is satisfied.

    The completeness check runs inside change_status via the workflow service,
    so the six required fields are validated against the values being written
    here rather than whatever happened to be on the row beforehand.
    """
    closed_date = closed_date or local_today()

    bug.closure_remarks = closure_remarks
    bug.closed_by = actor
    bug.closed_date = closed_date
    bug.closed_at = local_now()
    if verification_result:
        bug.verification_result = verification_result
    bug.updated_by = getattr(actor, "unique_id", None)
    bug.save(update_fields=[
        "closure_remarks", "closed_by", "closed_date", "closed_at",
        "verification_result", "updated_by", "updated_at",
    ])

    record_audit(action=AuditAction.CLOSURE, entity=bug, actor=actor,
                 new_value=closure_remarks[:200], remarks=remarks, request=request)

    return change_status(bug=bug, to_status=BugStatus.CLOSED, actor=actor,
                         remarks=remarks or "Bug closed.", request=request)


@transaction.atomic
def reopen_bug(*, bug, actor, reopen_reason, request=None):
    """Reopen a closed or resolved bug, preserving the closure record.

    Closure fields are deliberately left intact: the previous closure is part
    of the bug's history (spec 65), and reopen_count plus BugReopenHistory
    record that it happened.
    """
    BugReopenHistory.objects.create(
        bug=bug, reopen_reason=reopen_reason, reopened_by=actor,
    )

    bug.reopen_count = (bug.reopen_count or 0) + 1
    bug.last_reopened_at = local_now()
    bug.updated_by = getattr(actor, "unique_id", None)
    bug.save(update_fields=[
        "reopen_count", "last_reopened_at", "updated_by", "updated_at",
    ])

    record_audit(action=AuditAction.REOPEN, entity=bug, actor=actor,
                 new_value=reopen_reason[:200], request=request)

    return change_status(
        bug=bug, to_status=BugStatus.REOPENED, actor=actor,
        remarks=reopen_reason, payload={"reopen_reason": reopen_reason},
        request=request,
    )
