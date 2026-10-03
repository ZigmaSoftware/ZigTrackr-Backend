"""Technical validation before classification (spec 10).

Pure: takes a ParsedMail, returns a verdict. No database, no I/O.

The ordering below is spec 10.1's, and it is deliberate -- cheap structural
checks first, so a bounce is recognised before anything expensive happens to it.
"""

import re
from dataclasses import dataclass

from apps.classification.services.normalize import normalize_subject, normalize_text
from apps.mail_intake.constants import (
    AUTO_REPLY_SUBJECT_PATTERNS,
    BOUNCE_SUBJECT_PATTERNS,
    MailErrorCode,
)
from apps.mail_intake.services.mail_normalizer import (
    clean_readable_body,
    has_meaningful_content,
    html_to_text,
)

# Deliberately permissive. This rejects only the obviously broken; full RFC 5322
# address parsing would reject real addresses that mail servers accept, and the
# cost of a false rejection here is a silently discarded support request.
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

_BOUNCE_SENDERS = ("mailer-daemon@", "postmaster@")
_NO_REPLY_LOCAL_RE = re.compile(
    r"^(?:no[-_.]?reply|donotreply|do[-_.]?not[-_.]?reply)$",
    flags=re.IGNORECASE,
)
_PROMOTIONAL_SUBJECT_TERMS = (
    "advertisement",
    "newsletter",
    "unsubscribe",
    "marketing",
    "webinar",
    "limited time offer",
    "exclusive offer",
    "special offer",
    "clearance sale",
    "black friday",
    "cyber monday",
)
_PROMOTIONAL_BODY_MARKERS = (
    "unsubscribe",
    "click here",
    "shop now",
    "limited time",
    "exclusive offer",
    "special offer",
    "promo code",
    "discount code",
    "% off",
    "act now",
)
_TICKET_MARKER_RE = re.compile(r"^\s*\[(?:BUG|SERVICE|ACCESS)\]", flags=re.IGNORECASE)


@dataclass(frozen=True)
class ValidationResult:
    is_valid: bool
    error_code: str = ""
    reason: str = ""
    is_auto_reply: bool = False
    is_bounce: bool = False


def is_valid_address(value):
    return bool(value) and bool(_EMAIL_RE.match(value.strip()))


def is_no_reply(parsed):
    """Detect senders that explicitly do not accept replies."""
    local_part = (parsed.from_email or "").strip().lower().rsplit("@", 1)[0]
    return bool(local_part and _NO_REPLY_LOCAL_RE.fullmatch(local_part))


def _contains_term(value, term):
    return bool(re.search(rf"(?<!\w){re.escape(term)}(?!\w)", value or ""))


def is_promotional(parsed):
    """Detect common bulk-marketing signals without rejecting issue wording."""
    if _TICKET_MARKER_RE.match(parsed.subject or ""):
        return False

    headers = parsed.headers or {}
    if headers.get("list_unsubscribe") or headers.get("x_campaign_id"):
        return True

    subject = normalize_subject(parsed.subject)
    if any(_contains_term(subject, term) for term in _PROMOTIONAL_SUBJECT_TERMS):
        return True

    body = normalize_text(html_to_text(parsed.body_html) or parsed.body_text or "")
    markers = [marker for marker in _PROMOTIONAL_BODY_MARKERS if _contains_term(body, marker)]
    return "unsubscribe" in markers or len(markers) >= 2


def is_auto_reply(parsed):
    """Spec 10.2: headers first, then subject patterns."""
    headers = parsed.headers or {}

    auto_submitted = (headers.get("auto_submitted") or "").strip().lower()
    # "no" is the explicit marker for ordinary human mail; anything else here
    # (auto-replied, auto-generated) means a robot sent it.
    if auto_submitted and auto_submitted != "no":
        return True

    precedence = (headers.get("precedence") or "").strip().lower()
    if precedence in {"bulk", "auto_reply", "junk", "list"}:
        return True

    if (headers.get("x_autoreply") or "").strip():
        return True
    if (headers.get("x_autorespond") or "").strip():
        return True

    subject = (parsed.subject or "").strip().lower()
    return any(pattern in subject for pattern in AUTO_REPLY_SUBJECT_PATTERNS)


def is_bounce(parsed):
    headers = parsed.headers or {}

    if (headers.get("content_type") or "").lower() == "multipart/report":
        if "delivery-status" in (headers.get("report_type") or "").lower():
            return True
        return True

    sender = (parsed.from_email or "").lower()
    if any(sender.startswith(prefix) for prefix in _BOUNCE_SENDERS):
        return True

    # An empty Return-Path is the standard marker for a system-generated
    # notification that must not itself be replied to.
    if (headers.get("return_path") or "").strip() in {"<>", ""}and sender.startswith("mailer-daemon"):
        return True

    subject = (parsed.subject or "").strip().lower()
    return any(pattern in subject for pattern in BOUNCE_SUBJECT_PATTERNS)


def validate_content(*, subject, cleaned_body):
    if not has_meaningful_content(subject, cleaned_body):
        return ValidationResult(
            False, MailErrorCode.EMPTY_CONTENT, "Message has neither a subject nor a body.",
        )
    return ValidationResult(True)


def validate_mail(
    parsed, *, allowed_recipients=(), max_size_bytes=0, normalized_body="",
    cleaned_body=None,
):
    """Decide whether this message may become a ticket."""
    # ---- SENDER ----
    if not parsed.from_email:
        return ValidationResult(False, MailErrorCode.NO_SENDER, "Message has no sender address.")
    if not is_valid_address(parsed.from_email):
        return ValidationResult(
            False, MailErrorCode.INVALID_SENDER,
            f"Sender address is not valid: {parsed.from_email[:120]}",
        )

    # ---- AUTOMATED MAIL ----
    # Checked before recipient and content: a bounce addressed oddly or with an
    # empty body is still a bounce, and reporting it as such is more useful.
    if is_bounce(parsed):
        return ValidationResult(
            False, MailErrorCode.BOUNCE, "Message is a delivery failure notification.",
            is_bounce=True,
        )
    if is_promotional(parsed):
        return ValidationResult(
            False, MailErrorCode.PROMOTIONAL_MAIL, "Message is promotional mail.",
        )
    if is_auto_reply(parsed):
        return ValidationResult(
            False, MailErrorCode.AUTO_REPLY, "Message is an automatic reply.",
            is_auto_reply=True,
        )
    if is_no_reply(parsed):
        return ValidationResult(
            False, MailErrorCode.NO_REPLY, "Sender does not accept replies.",
        )

    # ---- RECIPIENT ----
    # An empty allow-list accepts everything on purpose: a misconfigured alias
    # list must not silently reject every incoming message.
    if allowed_recipients:
        allowed = {a.strip().lower() for a in allowed_recipients if a and a.strip()}
        recipients = {
            a.lower() for a in (tuple(parsed.to_emails) + tuple(parsed.cc_emails)) if a
        }
        if not (recipients & allowed):
            return ValidationResult(
                False, MailErrorCode.RECIPIENT_MISMATCH,
                "Message is not addressed to the configured intake mailbox.",
            )

    # ---- SIZE ----
    if max_size_bytes and parsed.raw_size_bytes > max_size_bytes:
        return ValidationResult(
            False, MailErrorCode.MESSAGE_TOO_LARGE,
            f"Message is {parsed.raw_size_bytes} bytes, over the "
            f"{max_size_bytes} byte limit.",
        )

    # ---- CONTENT ----
    # Last, because it is the only check needing normalised text. A subject alone
    # is enough: "[BUG] Invoice screen blank" is a complete report.
    if cleaned_body is None:
        cleaned_body = normalized_body or clean_readable_body(
            parsed.body_text, parsed.body_html
        )
    return validate_content(subject=parsed.subject, cleaned_body=cleaned_body)

