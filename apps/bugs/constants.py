"""Workflow enums and status groupings.

Status is a fixed TextChoices set rather than a master table: the spec 29
transition graph and the spec 30 validation rules are written against exact
values, and spec 40 explicitly sanctions "controlled code choices if the
workflow is fixed". A user-editable status master would allow adding a status
the state machine has never heard of, producing bugs that can never be closed.
"""

from django.db import models


class BugStatus(models.TextChoices):
    NEW = "NEW", "New"
    ASSIGNED = "ASSIGNED", "Assigned"
    IN_PROGRESS = "IN_PROGRESS", "In Progress"
    PENDING = "PENDING", "Pending"
    TESTING = "TESTING", "Testing / Verification"
    ON_HOLD = "ON_HOLD", "On Hold"
    RESOLVED = "RESOLVED", "Resolved"
    CLOSED = "CLOSED", "Closed"
    REOPENED = "REOPENED", "Reopened"
    REJECTED = "REJECTED", "Rejected / Not a Bug"


class Environment(models.TextChoices):
    PRODUCTION = "PRODUCTION", "Production"
    UAT = "UAT", "UAT"
    STAGING = "STAGING", "Staging"
    DEVELOPMENT = "DEVELOPMENT", "Development"
    LOCAL = "LOCAL", "Local"


class VerificationResult(models.TextChoices):
    PASSED = "PASSED", "Passed"
    FAILED = "FAILED", "Failed"
    PARTIAL = "PARTIAL", "Partially Passed"


class TestResult(models.TextChoices):
    PASSED = "PASSED", "Passed"
    FAILED = "FAILED", "Failed"
    BLOCKED = "BLOCKED", "Blocked"


class AttachmentContext(models.TextChoices):
    BUG = "BUG", "Bug"
    UPDATE = "UPDATE", "Daily Update"
    TESTING = "TESTING", "Testing"
    CLOSURE = "CLOSURE", "Closure"


# ---- STATUS GROUPINGS ----

# Spec 11: the owner must post a daily update while a bug sits in these.
DAILY_UPDATE_REQUIRED_STATUSES = (
    BugStatus.ASSIGNED,
    BugStatus.IN_PROGRESS,
    BugStatus.PENDING,
    BugStatus.TESTING,
    BugStatus.ON_HOLD,
)

# Spec 12: overdue only applies to bugs that are not finished. A rejected bug is
# not "late", so it counts as terminal alongside closed.
TERMINAL_STATUSES = (BugStatus.CLOSED, BugStatus.REJECTED)

OPEN_STATUSES = tuple(s for s in BugStatus.values if s not in TERMINAL_STATUSES)

# ---- AGING BANDS (spec 13) ----
AGING_BANDS = (
    ("NORMAL", "Normal"),
    ("ATTENTION", "Attention"),
    ("WARNING", "Warning"),
    ("CRITICAL", "Critical Aging"),
)
