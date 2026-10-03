"""RFC822 parsing into a plain dataclass (spec 8).

Pure: no database, no network, no Django models. That makes this the most
testable module in the pipeline, and the test suite leans on it heavily.

email.policy.default is used throughout rather than the legacy compat32 policy:
it decodes RFC 2047 encoded-words ("=?UTF-8?B?...?=") and charset-tagged bodies
for us, which is most of what makes real-world mail parsing tedious.
"""

import email
import email.policy
import hashlib
import logging
from dataclasses import dataclass, field
from email.utils import getaddresses, parseaddr, parsedate_to_datetime

from django.utils import timezone

logger = logging.getLogger(__name__)

# RFC 5322 sets no hard limit on Message-ID, but 255 is the practical cap and
# matches the column. Truncation would corrupt threading, so it is logged.
MAX_MESSAGE_ID_LENGTH = 255
MAX_SUBJECT_LENGTH = 500
MAX_ADDRESS_LENGTH = 320


@dataclass(frozen=True)
class ParsedAttachment:
    file_name: str
    content: bytes
    content_type: str = ""
    content_id: str = ""
    is_inline: bool = False


@dataclass(frozen=True)
class ParsedMail:
    provider_message_id: str = ""
    provider_thread_id: str = ""
    message_id: str = ""
    in_reply_to: str = ""
    references_header: str = ""
    from_email: str = ""
    from_name: str = ""
    to_emails: tuple = ()
    cc_emails: tuple = ()
    reply_to_email: str = ""
    subject: str = ""
    body_text: str = ""
    body_html: str = ""
    received_at: object = None
    raw_size_bytes: int = 0
    headers: dict = field(default_factory=dict)
    attachments: tuple = ()
    date_header_missing: bool = False


def strip_angle_brackets(value):
    """Normalise a Message-ID to bare form.

    Stored without brackets everywhere -- MailIntake.message_id and
    SupportTicket.ack_message_id both -- so thread lookups can compare the two
    columns directly. Inconsistent bracketing here is the classic silent
    threading failure.
    """
    if not value:
        return ""
    return str(value).strip().strip("<>").strip()


def parse_message_ids(header_value, limit=None):
    """Split a References/In-Reply-To header into bare message ids."""
    if not header_value:
        return []
    tokens = [strip_angle_brackets(t) for t in str(header_value).split()]
    tokens = [t for t in tokens if t]
    if limit is not None and len(tokens) > limit:
        # The useful references are at the tail: the immediate parents.
        tokens = tokens[-limit:]
    return tokens


def _header(message, name, default=""):
    try:
        value = message.get(name)
    except Exception:  # pragma: no cover - malformed header object
        return default
    if value is None:
        return default
    return str(value).strip()


def _addresses(message, name):
    raw = message.get_all(name, [])
    if not raw:
        return ()
    out = []
    for _, addr in getaddresses([str(r) for r in raw]):
        addr = (addr or "").strip().lower()[:MAX_ADDRESS_LENGTH]
        if addr and addr not in out:
            out.append(addr)
    return tuple(out)


def _received_at(message):
    """The Date header, or now() when it is absent or unparseable."""
    raw = _header(message, "Date")
    if raw:
        try:
            parsed = parsedate_to_datetime(raw)
            if parsed is not None:
                if timezone.is_naive(parsed):
                    parsed = timezone.make_aware(parsed, timezone.utc)
                return parsed, False
        except (TypeError, ValueError):
            logger.warning("Unparseable Date header: %r", raw[:100])
    return timezone.now(), True


def _extract_bodies(message):
    text = html = ""
    try:
        plain_part = message.get_body(preferencelist=("plain",))
        if plain_part is not None:
            text = plain_part.get_content()
    except Exception:
        logger.warning("Could not extract text/plain body", exc_info=True)
    try:
        html_part = message.get_body(preferencelist=("html",))
        if html_part is not None:
            html = html_part.get_content()
    except Exception:
        logger.warning("Could not extract text/html body", exc_info=True)
    return (text or "").strip(), (html or "").strip()


def _extract_attachments(message):
    out = []
    try:
        parts = list(message.iter_attachments())
    except Exception:  # pragma: no cover - defensive
        logger.warning("Could not iterate attachments", exc_info=True)
        return ()

    for part in parts:
        try:
            payload = part.get_payload(decode=True)
        except Exception:
            logger.warning("Could not decode an attachment payload", exc_info=True)
            continue
        if payload is None:
            continue

        disposition = (part.get_content_disposition() or "").lower()
        content_id = strip_angle_brackets(part.get("Content-ID", ""))
        out.append(
            ParsedAttachment(
                # The sender's filename is display data only; it never becomes
                # a path (spec 45).
                file_name=part.get_filename() or "attachment",
                content=payload,
                content_type=part.get_content_type() or "",
                content_id=content_id,
                is_inline=disposition == "inline" or bool(content_id),
            )
        )
    return tuple(out)


def parse_raw_email(raw_bytes, *, provider_message_id="", provider_thread_id=""):
    """Turn raw RFC822 bytes into a ParsedMail."""
    message = email.message_from_bytes(raw_bytes, policy=email.policy.default)

    message_id = strip_angle_brackets(_header(message, "Message-ID"))
    if len(message_id) > MAX_MESSAGE_ID_LENGTH:
        logger.warning("Truncating over-long Message-ID (%d chars)", len(message_id))
        message_id = message_id[:MAX_MESSAGE_ID_LENGTH]

    from_name, from_email = parseaddr(_header(message, "From"))
    _, reply_to = parseaddr(_header(message, "Reply-To"))
    received_at, date_missing = _received_at(message)
    body_text, body_html = _extract_bodies(message)

    return ParsedMail(
        provider_message_id=str(provider_message_id or ""),
        provider_thread_id=str(provider_thread_id or ""),
        message_id=message_id,
        in_reply_to=strip_angle_brackets(_header(message, "In-Reply-To"))[:MAX_MESSAGE_ID_LENGTH],
        references_header=_header(message, "References"),
        from_email=(from_email or "").strip().lower()[:MAX_ADDRESS_LENGTH],
        from_name=(from_name or "").strip()[:150],
        to_emails=_addresses(message, "To"),
        cc_emails=_addresses(message, "Cc"),
        reply_to_email=(reply_to or "").strip().lower()[:MAX_ADDRESS_LENGTH],
        subject=_header(message, "Subject")[:MAX_SUBJECT_LENGTH],
        body_text=body_text,
        body_html=body_html,
        received_at=received_at,
        raw_size_bytes=len(raw_bytes),
        headers={
            "auto_submitted": _header(message, "Auto-Submitted"),
            "precedence": _header(message, "Precedence"),
            "x_autoreply": _header(message, "X-Autoreply"),
            "x_autorespond": _header(message, "X-Autorespond"),
            "x_auto_response_suppress": _header(message, "X-Auto-Response-Suppress"),
            "list_unsubscribe": _header(message, "List-Unsubscribe"),
            "list_id": _header(message, "List-ID"),
            "x_campaign_id": _header(message, "X-Campaign-ID"),
            "return_path": _header(message, "Return-Path"),
            "content_type": (message.get_content_type() or ""),
            "report_type": str(message.get_param("report-type", "") or ""),
        },
        attachments=_extract_attachments(message),
        date_header_missing=date_missing,
    )


def body_fingerprint(text):
    """SHA-256 of normalised body text, used in the de-duplication fallback."""
    return hashlib.sha256((text or "").encode("utf-8", "replace")).hexdigest()
