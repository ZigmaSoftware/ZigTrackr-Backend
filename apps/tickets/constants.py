"""Support ticket vocabulary (spec 22).

TextChoices rather than master tables, matching apps/bugs/constants.py: these
values are branched on in code, so they are part of the program, not data a user
may rename.
"""

from django.db import models


class TicketSource(models.TextChoices):
    MANUAL = "MANUAL", "Manual"
    EMAIL = "EMAIL", "Email"
    WHATSAPP = "WHATSAPP", "WhatsApp"
    IN_PERSON = "IN_PERSON", "In Person"


# Sources a human may pick when raising a ticket by hand. EMAIL here means the
# request arrived by mail but was keyed in manually, so such a ticket has no
# linked MailIntake row -- unlike one the intake pipeline created.
MANUAL_TICKET_SOURCES = (
    TicketSource.EMAIL,
    TicketSource.WHATSAPP,
    TicketSource.IN_PERSON,
)


class TicketType(models.TextChoices):
    BUG = "BUG", "Bug"
    SERVICE_REQUEST = "SERVICE_REQUEST", "Service Request"
    ACCESS_REQUEST = "ACCESS_REQUEST", "Access Request"
    UNKNOWN = "UNKNOWN", "Unknown"


class ClassificationMethod(models.TextChoices):
    MANUAL = "MANUAL", "Manual"
    RULE_BASED = "RULE_BASED", "Rule Based"
    # Reserved for Phase 2 so the column never needs widening. Nothing in Phase 1
    # may write this value (spec 22).
    AI = "AI", "AI Assisted"


class ClassificationStatus(models.TextChoices):
    AUTO_CLASSIFIED = "AUTO_CLASSIFIED", "Auto Classified"
    NEEDS_REVIEW = "NEEDS_REVIEW", "Needs Review"
    HUMAN_CONFIRMED = "HUMAN_CONFIRMED", "Human Confirmed"


class TicketStatus(models.TextChoices):
    NEW = "NEW", "New"
    NEEDS_REVIEW = "NEEDS_REVIEW", "Needs Review"
    CONFIRMED = "CONFIRMED", "Confirmed"
    ASSIGNED = "ASSIGNED", "Assigned"
    IN_PROGRESS = "IN_PROGRESS", "In Progress"
    REOPENED = "REOPENED", "Reopened"
    PENDING = "PENDING", "Pending"
    ON_HOLD = "ON_HOLD", "On Hold"
    # The developer's work is done and a tester must verify it before closure.
    # Named to match BugStatus.TESTING so the shared status filter treats a
    # ticket and its linked bug as one thing.
    TESTING = "TESTING", "Testing / Verification"
    PENDING_APPROVAL = "PENDING_APPROVAL", "Pending Approval"
    APPROVED = "APPROVED", "Approved"
    COMPLETED = "COMPLETED", "Completed"
    CLOSED = "CLOSED", "Closed"
    REJECTED = "REJECTED", "Rejected"


class TicketUpdateSource(models.TextChoices):
    USER = "USER", "User"
    EMAIL = "EMAIL", "Email"
    SYSTEM = "SYSTEM", "System"


TERMINAL_TICKET_STATUSES = (TicketStatus.CLOSED, TicketStatus.REJECTED)

OPEN_TICKET_STATUSES = (
    TicketStatus.NEW,
    TicketStatus.NEEDS_REVIEW,
    TicketStatus.CONFIRMED,
    TicketStatus.ASSIGNED,
    TicketStatus.IN_PROGRESS,
    TicketStatus.REOPENED,
    TicketStatus.PENDING,
    TicketStatus.ON_HOLD,
    TicketStatus.TESTING,
    TicketStatus.PENDING_APPROVAL,
    TicketStatus.APPROVED,
)

# The work flow a ticket follows once a developer picks it up. Start moves it to
# IN_PROGRESS; from there the developer may park it, note progress, or hand it
# to a tester, who alone closes it.
WORK_TRANSITIONS = {
    TicketStatus.ASSIGNED: (TicketStatus.IN_PROGRESS,),
    TicketStatus.NEW: (TicketStatus.IN_PROGRESS,),
    TicketStatus.CONFIRMED: (TicketStatus.IN_PROGRESS,),
    TicketStatus.APPROVED: (TicketStatus.IN_PROGRESS,),
    TicketStatus.IN_PROGRESS: (TicketStatus.PENDING, TicketStatus.ON_HOLD, TicketStatus.TESTING),
    TicketStatus.PENDING: (TicketStatus.IN_PROGRESS, TicketStatus.TESTING),
    TicketStatus.ON_HOLD: (TicketStatus.IN_PROGRESS, TicketStatus.TESTING),
    TicketStatus.TESTING: (TicketStatus.CLOSED, TicketStatus.IN_PROGRESS),
    TicketStatus.REOPENED: (TicketStatus.IN_PROGRESS, TicketStatus.CLOSED),
}

# Access requests move through approval before anything is implemented. The
# service layer checks this ordering; a permission codename cannot express it.
ACCESS_APPROVAL_REQUIRED_BEFORE = TicketStatus.APPROVED
