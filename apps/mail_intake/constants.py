"""Mail intake vocabulary (spec 9, 44)."""

from django.db import models


class MailProcessingStatus(models.TextChoices):
    RECEIVED = "RECEIVED", "Received"
    VALIDATING = "VALIDATING", "Validating"
    REJECTED = "REJECTED", "Rejected"
    DUPLICATE = "DUPLICATE", "Duplicate"
    THREAD_UPDATE = "THREAD_UPDATE", "Thread Update"
    READY_FOR_CLASSIFICATION = "READY_FOR_CLASSIFICATION", "Ready for Classification"
    CLASSIFIED = "CLASSIFIED", "Classified"
    NEEDS_REVIEW = "NEEDS_REVIEW", "Needs Review"
    TICKET_CREATED = "TICKET_CREATED", "Ticket Created"
    PROCESSING_FAILED = "PROCESSING_FAILED", "Processing Failed"


# Reached a resting place; the retry sweep leaves these alone.
TERMINAL_MAIL_STATUSES = (
    MailProcessingStatus.REJECTED,
    MailProcessingStatus.DUPLICATE,
    MailProcessingStatus.THREAD_UPDATE,
    MailProcessingStatus.TICKET_CREATED,
)

# Mid-pipeline, so a later run can resume them from persisted state.
RETRYABLE_MAIL_STATUSES = (
    MailProcessingStatus.RECEIVED,
    MailProcessingStatus.VALIDATING,
    MailProcessingStatus.READY_FOR_CLASSIFICATION,
    MailProcessingStatus.CLASSIFIED,
    MailProcessingStatus.PROCESSING_FAILED,
)


class MailErrorCode(models.TextChoices):
    # ---- PERMANENT: retrying cannot change the outcome ----
    NO_SENDER = "NO_SENDER", "No sender address"
    INVALID_SENDER = "INVALID_SENDER", "Malformed sender address"
    RECIPIENT_MISMATCH = "RECIPIENT_MISMATCH", "Not addressed to the intake mailbox"
    EMPTY_CONTENT = "EMPTY_CONTENT", "No subject or body"
    PROMOTIONAL_MAIL = "PROMOTIONAL_MAIL", "Promotional mail"
    NO_REPLY = "NO_REPLY", "No-reply sender"
    AUTO_REPLY = "AUTO_REPLY", "Automatic reply"
    BOUNCE = "BOUNCE", "Delivery failure notification"
    DUPLICATE = "DUPLICATE", "Already processed"
    MESSAGE_TOO_LARGE = "MESSAGE_TOO_LARGE", "Message exceeds size limit"
    ATTACHMENT_REJECTED = "ATTACHMENT_REJECTED", "Attachment failed validation"
    PARSE_ERROR = "PARSE_ERROR", "Message could not be parsed"

    # ---- TEMPORARY: the same message may succeed later ----
    IMAP_ERROR = "IMAP_ERROR", "Mailbox connection problem"
    STORAGE_ERROR = "STORAGE_ERROR", "File storage problem"
    DB_ERROR = "DB_ERROR", "Database problem"
    UNEXPECTED_ERROR = "UNEXPECTED_ERROR", "Unexpected error"


# Splitting these as frozensets next to the enum is what makes spec 44's
# "distinguish temporary from permanent" enforceable rather than aspirational.
PERMANENT_ERROR_CODES = frozenset({
    MailErrorCode.NO_SENDER,
    MailErrorCode.INVALID_SENDER,
    MailErrorCode.RECIPIENT_MISMATCH,
    MailErrorCode.EMPTY_CONTENT,
    MailErrorCode.PROMOTIONAL_MAIL,
    MailErrorCode.NO_REPLY,
    MailErrorCode.AUTO_REPLY,
    MailErrorCode.BOUNCE,
    MailErrorCode.DUPLICATE,
    MailErrorCode.MESSAGE_TOO_LARGE,
    MailErrorCode.ATTACHMENT_REJECTED,
    MailErrorCode.PARSE_ERROR,
})

TEMPORARY_ERROR_CODES = frozenset({
    MailErrorCode.IMAP_ERROR,
    MailErrorCode.STORAGE_ERROR,
    MailErrorCode.DB_ERROR,
    MailErrorCode.UNEXPECTED_ERROR,
})

# Subject prefixes that mark an automatic reply (spec 10.2). Matched against the
# normalized subject.
AUTO_REPLY_SUBJECT_PATTERNS = (
    "automatic reply",
    "auto reply",
    "autoreply",
    "out of office",
    "out-of-office",
    "away from the office",
)

BOUNCE_SUBJECT_PATTERNS = (
    "delivery status notification",
    "mail delivery failed",
    "undelivered mail",
    "undeliverable",
    "returned mail",
    "delivery failure",
    "failure notice",
)

# Addresses that must never receive an acknowledgement: replying to them is how
# mail loops start.
NO_ACK_LOCAL_PARTS = (
    "mailer-daemon",
    "postmaster",
    "no-reply",
    "noreply",
    "donotreply",
    "do-not-reply",
    "bounce",
    "bounces",
)

MAX_REFERENCE_TOKENS = 20
