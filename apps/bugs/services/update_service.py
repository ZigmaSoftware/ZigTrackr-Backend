"""Daily updates (spec 10, 11).

Every BugUpdate must be created through add_update(). The Bug row carries
denormalised latest_* fields that the Update Pending query depends on, and they
are only trustworthy if refreshed in the same transaction as the update itself.
Creating BugUpdate rows directly anywhere else silently corrupts the dashboard.
"""

from django.db import transaction

from apps.audit.models import AuditAction
from apps.bugs.models import BugUpdate
from common.services.audit import record_audit
from common.utils.dates import local_now, local_today


@transaction.atomic
def add_update(*, bug, actor, update_text, remarks="", next_action="",
               expected_completion_date=None, is_system_generated=False,
               request=None):
    now = local_now()
    today = local_today()

    update = BugUpdate.objects.create(
        bug=bug,
        status=bug.status,
        owner=bug.owner,
        update_text=update_text,
        remarks=remarks,
        next_action=next_action,
        expected_completion_date=expected_completion_date,
        updated_by=actor,
        update_date=today,
        is_system_generated=is_system_generated,
    )

    # Refresh the denormalised current state the list and dashboard read.
    bug.latest_remarks = remarks or update_text
    bug.latest_update_at = now
    bug.latest_update_date = today
    if next_action:
        bug.next_action = next_action
    if expected_completion_date:
        bug.expected_closure_date = expected_completion_date
    bug.save(update_fields=[
        "latest_remarks", "latest_update_at", "latest_update_date",
        "next_action", "expected_closure_date", "updated_at",
    ])

    if not is_system_generated:
        record_audit(action=AuditAction.UPDATE_ADDED, entity=bug, actor=actor,
                     new_value=update_text[:200], request=request)
    return update
