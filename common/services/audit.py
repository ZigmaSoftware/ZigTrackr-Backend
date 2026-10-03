"""Generic audit service (spec 41).

Called explicitly from the service layer rather than wired to post_save signals.
Signals cannot see *who* acted without a thread-local hack, cannot tell a
priority change from a typo fix, and fire during fixture loads and data
migrations. Spec 41 asks for semantic actions (ASSIGN, REOPEN, CLOSURE), not row
diffs, so an explicit call at the point of the business decision is both more
accurate and more readable.
"""

import logging

logger = logging.getLogger(__name__)


def _stringify(value):
    if value is None:
        return ""
    if hasattr(value, "display_name"):
        return str(value.display_name)
    return str(value)


def _client_ip(request):
    if request is None:
        return None
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR") or None


def record_audit(
    *, action, entity, actor=None, field_name="", old_value=None, new_value=None,
    remarks="", metadata=None, request=None,
):
    """Append one audit_log row.

    Deliberately inside the caller's transaction: a rolled-back operation must
    not leave an audit trace of something that never happened.
    """
    from apps.audit.models import AuditLog

    return AuditLog.objects.create(
        entity_type=type(entity).__name__,
        entity_id=getattr(entity, "pk", None),
        entity_unique_id=getattr(entity, "unique_id", None),
        entity_label=str(getattr(entity, "bug_no", "") or entity)[:120],
        action=action,
        field_name=field_name,
        old_value=_stringify(old_value)[:5000],
        new_value=_stringify(new_value)[:5000],
        remarks=remarks,
        metadata=metadata or {},
        performed_by=actor if getattr(actor, "pk", None) else None,
        performed_by_name=(getattr(actor, "display_name", "") or "")[:150],
        ip_address=_client_ip(request),
        user_agent=(request.META.get("HTTP_USER_AGENT", "")[:500] if request else ""),
    )


def record_field_changes(*, entity, actor, changes, action, request=None):
    """Write one row per genuinely changed field.

    `changes` is {field_name: (old, new)}. No-op changes are skipped so the
    audit log does not fill with noise from unchanged form resubmissions.
    """
    from apps.audit.models import AuditLog

    rows = []
    for field_name, (old, new) in changes.items():
        if _stringify(old) == _stringify(new):
            continue
        rows.append(AuditLog(
            entity_type=type(entity).__name__,
            entity_id=getattr(entity, "pk", None),
            entity_unique_id=getattr(entity, "unique_id", None),
            entity_label=str(getattr(entity, "bug_no", "") or entity)[:120],
            action=action,
            field_name=field_name,
            old_value=_stringify(old)[:5000],
            new_value=_stringify(new)[:5000],
            performed_by=actor if getattr(actor, "pk", None) else None,
            performed_by_name=(getattr(actor, "display_name", "") or "")[:150],
            ip_address=_client_ip(request),
            user_agent=(request.META.get("HTTP_USER_AGENT", "")[:500] if request else ""),
        ))
    if rows:
        AuditLog.objects.bulk_create(rows)
    return rows
