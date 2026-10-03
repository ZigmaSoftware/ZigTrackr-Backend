"""Testing and verification (spec 39.6, 30)."""

from django.db import transaction

from apps.audit.models import AuditAction
from apps.bugs.constants import BugStatus, TestResult, VerificationResult
from apps.bugs.models import BugTestingHistory
from apps.bugs.services.status_service import change_status
from common.services.audit import record_audit
from common.utils.dates import local_now

# A failed or blocked test sends the bug back to the developer; only a pass
# leaves it in testing awaiting resolution.
RESULT_TO_VERIFICATION = {
    TestResult.PASSED: VerificationResult.PASSED,
    TestResult.FAILED: VerificationResult.FAILED,
    TestResult.BLOCKED: VerificationResult.PARTIAL,
}


@transaction.atomic
def record_test(*, bug, actor, test_result, test_remarks="", request=None):
    BugTestingHistory.objects.create(
        bug=bug, tested_by=actor, test_result=test_result, test_remarks=test_remarks,
    )

    bug.tested_by = actor
    bug.verification_result = RESULT_TO_VERIFICATION.get(test_result, "")
    bug.verification_remarks = test_remarks
    bug.verified_at = local_now()
    bug.updated_by = getattr(actor, "unique_id", None)
    bug.save(update_fields=[
        "tested_by", "verification_result", "verification_remarks",
        "verified_at", "updated_by", "updated_at",
    ])

    record_audit(action=AuditAction.TESTING_RESULT, entity=bug, actor=actor,
                 field_name="verification_result", new_value=test_result,
                 remarks=test_remarks, request=request)

    from apps.tickets.models import SupportTicket
    from apps.tickets.services.activity_service import record_activity

    ticket = SupportTicket.objects.filter(bug=bug, is_deleted=False).first()
    if ticket:
        label = TestResult(test_result).label
        record_activity(
            ticket=ticket, event_type=f"TEST_{test_result}",
            title=f"Test {label.lower()}",
            description=f"{actor.display_name} recorded a {label.lower()} test. {test_remarks}".strip(),
            public_description=f"Verification {label.lower()}.",
            actor=actor,
        )

    # A failed test returns the bug to In Progress (spec 29: Testing -> In Progress).
    if test_result in (TestResult.FAILED, TestResult.BLOCKED):
        if bug.status == BugStatus.TESTING:
            change_status(
                bug=bug, to_status=BugStatus.IN_PROGRESS, actor=actor,
                remarks=f"Test {TestResult(test_result).label.lower()}: {test_remarks}".strip(": "),
                request=request,
            )

    return bug
