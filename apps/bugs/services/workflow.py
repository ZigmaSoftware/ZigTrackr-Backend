"""The bug status state machine (spec 29, 30).

This module is the single source of truth for status transitions and the field
requirements that guard them. Nothing else in the codebase may change
`bug.status`.

That matters because spec 18 requires the many sidebar views (My Bugs, Overdue,
Testing, Closed...) to be filtered views over one domain rather than duplicated
workflows. Centralising the rules here is what makes that true in practice: an
action endpoint, a bulk operation and a future import all pass through the same
gate and cannot drift apart.
"""

from apps.bugs.constants import BugStatus
from common.exceptions.domain import TransitionNotAllowed, WorkflowValidationError

# ---- TRANSITION MAP (spec 29) ----
ALLOWED_TRANSITIONS = {
    BugStatus.NEW: {BugStatus.ASSIGNED, BugStatus.REJECTED},
    BugStatus.ASSIGNED: {BugStatus.IN_PROGRESS, BugStatus.ON_HOLD},
    BugStatus.IN_PROGRESS: {BugStatus.TESTING, BugStatus.ON_HOLD, BugStatus.PENDING},
    BugStatus.PENDING: {BugStatus.IN_PROGRESS, BugStatus.ON_HOLD, BugStatus.TESTING},
    BugStatus.ON_HOLD: {BugStatus.IN_PROGRESS, BugStatus.PENDING, BugStatus.TESTING},
    BugStatus.TESTING: {BugStatus.RESOLVED, BugStatus.IN_PROGRESS},
    BugStatus.RESOLVED: {BugStatus.CLOSED, BugStatus.REOPENED},
    BugStatus.CLOSED: {BugStatus.REOPENED},
    BugStatus.REOPENED: {BugStatus.ASSIGNED, BugStatus.IN_PROGRESS},
    BugStatus.REJECTED: set(),
}

# ---- REQUIRED FIELDS PER TARGET STATUS (spec 30) ----
# field name -> human message used when the value is missing or blank.
REQUIRED_FIELDS = {
    BugStatus.IN_PROGRESS: {
        "owner": "An owner must be assigned before work can start.",
    },
    BugStatus.TESTING: {
        "resolution": "A resolution / fix summary is required before sending to testing.",
        "latest_remarks": "Developer remarks are required before sending to testing.",
    },
    BugStatus.ON_HOLD: {
        "hold_reason": "A hold reason is required.",
    },
    BugStatus.PENDING: {
        "pending_reason": "A pending reason is required.",
    },
    BugStatus.REJECTED: {
        "rejection_reason": "A rejection reason is required.",
    },
    BugStatus.RESOLVED: {
        "root_cause": "Root cause is required before resolving the bug.",
        "resolution": "Resolution is required before resolving the bug.",
        "resolved_date": "Resolved date is required before resolving the bug.",
    },
    BugStatus.CLOSED: {
        "root_cause": "Root cause is required before closing the bug.",
        "resolution": "Resolution is required before closing the bug.",
        "verification_result": "A verification / test result is required before closing the bug.",
        "closure_remarks": "Closure remarks are required before closing the bug.",
        "closed_date": "Closure date is required before closing the bug.",
        "closed_by": "Closed by is required before closing the bug.",
    },
    BugStatus.REOPENED: {
        "reopen_reason": "A reopen reason is required.",
    },
}

# Fields that are supplied by the action payload rather than read off the bug
# row. They are validated against the payload, never against the model.
PAYLOAD_ONLY_FIELDS = {"reopen_reason"}


def allowed_targets(from_status):
    return sorted(ALLOWED_TRANSITIONS.get(from_status, set()))


def can_transition(from_status, to_status):
    return to_status in ALLOWED_TRANSITIONS.get(from_status, set())


def check_transition(from_status, to_status):
    """Raise TransitionNotAllowed unless the move is legal."""
    if from_status == to_status:
        raise TransitionNotAllowed(
            f"The bug is already {BugStatus(to_status).label}.",
            from_status=from_status, to_status=to_status,
            allowed=allowed_targets(from_status),
        )
    if not can_transition(from_status, to_status):
        from_label = BugStatus(from_status).label
        to_label = BugStatus(to_status).label
        allowed = allowed_targets(from_status)
        allowed_labels = ", ".join(BugStatus(s).label for s in allowed) or "no further transitions"
        raise TransitionNotAllowed(
            f"Cannot move a bug from {from_label} to {to_label}. Allowed: {allowed_labels}.",
            from_status=from_status, to_status=to_status, allowed=allowed,
        )


def _is_blank(value):
    if value is None:
        return True
    if isinstance(value, str) and not value.strip():
        return True
    return False


def check_required_fields(bug, to_status, payload=None):
    """Validate every field required by `to_status`, reporting all misses at once.

    Collecting the failures rather than raising on the first one is deliberate:
    closing a bug needs six fields (spec 30), and surfacing them one per request
    would turn closure into a six-round guessing game.

    Values are read from `payload` first so a single request can supply the
    missing data and perform the transition together.
    """
    payload = payload or {}
    requirements = REQUIRED_FIELDS.get(to_status, {})
    errors = {}

    for field, message in requirements.items():
        if field in payload:
            value = payload.get(field)
        elif field in PAYLOAD_ONLY_FIELDS:
            value = None
        else:
            value = getattr(bug, field, None)
            # FKs: fall back to the raw id so an unsaved assignment still counts.
            if value is None:
                value = getattr(bug, f"{field}_id", None)

        if _is_blank(value):
            errors[field] = [message]

    if errors:
        raise WorkflowValidationError(errors)


def validate_transition(bug, to_status, payload=None, *, allow_reopened_verification=False):
    """Full gate: legality first, then completeness."""
    if not (allow_reopened_verification and bug.status == BugStatus.REOPENED
            and to_status == BugStatus.RESOLVED):
        check_transition(bug.status, to_status)
    check_required_fields(bug, to_status, payload=payload)
