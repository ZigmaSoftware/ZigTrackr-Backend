"""Turning a raw message into matchable text (spec 13).

Two separate outputs, and the distinction matters:

* `normalized_body` feeds classification. Quoted replies and signatures are
  stripped so a three-deep forward does not classify on somebody else's words.
* `body_html_sanitized` feeds the UI. That is a security boundary, handled in
  sanitize.py, not here.

The original subject and bodies are always kept untouched on the row (spec 13's
last line), so nothing here is lossy in the record.
"""

import hashlib
import html
import re
import unicodedata
from html.parser import HTMLParser

from apps.classification.services.normalize import (  # re-exported for one import site
    normalize_subject,
    normalize_text,
)

__all__ = [
    "clean_readable_body",
    "compute_dedupe_key",
    "has_meaningful_content",
    "html_to_text",
    "normalize_body",
    "normalize_subject",
    "normalize_text",
    "strip_greeting",
    "strip_quoted_blocks",
    "strip_signature",
]

# "On <date> <person> wrote:", including the wrapped variant Gmail produces.
_QUOTE_INTRO = re.compile(
    r"^\s*on .{0,200}?\bwrote:\s*$", flags=re.IGNORECASE | re.MULTILINE
)
_OUTLOOK_SEPARATOR = re.compile(
    r"^\s*-{2,}\s*original message\s*-{2,}\s*$", flags=re.IGNORECASE | re.MULTILINE
)
_OUTLOOK_HEADER_BLOCK = re.compile(
    r"^\s*(from|sent|to|subject|cc):\s.*$", flags=re.IGNORECASE | re.MULTILINE
)
# The RFC 3676 signature delimiter: a line containing exactly "-- ".
_SIGNATURE_DELIMITER = re.compile(r"^--\s?$", flags=re.MULTILINE)
_GREETING_LINE = re.compile(
    r"^\s*(?:hi|hello|hey|dear|greetings)(?:\s+[a-z0-9 ._/'’&-]{1,80})?\s*[,.]?\s*$"
    r"|^\s*good\s+(?:morning|afternoon|evening|day)"
    r"(?:\s+[a-z0-9 ._/'’&-]{1,80})?\s*[,.]?\s*$",
    flags=re.IGNORECASE,
)
_SIGNOFF = re.compile(
    r"^\s*(?:regards|best regards|kind regards|warm regards|with regards|"
    r"thanks\s*(?:&|and)\s*regards|thanks|thank you|cheers|sincerely|"
    r"yours truly|best wishes|sent from my [a-z0-9 ._-]+)[,.!]*\s*$",
    flags=re.IGNORECASE | re.MULTILINE,
)
_TRAILING_COURTESY = re.compile(
    r"(?:^[ \t]*|[ \t]+)(?:thank\s+you|thanks|cheers|regards|best regards|"
    r"kind regards|warm regards|with regards|sincerely|yours truly|best wishes)"
    r"\s*[.!]*\s*$",
    flags=re.IGNORECASE | re.MULTILINE,
)


class _TextExtractor(HTMLParser):
    """Strip tags to readable text, dropping script and style subtrees entirely.

    A regex tag-stripper is not used: it cannot tell that the *contents* of a
    <script> block must go too, and would happily emit JavaScript as body text.

    This is for matching and plain-text display only. It is NOT the sanitiser --
    see sanitize.py for what may be rendered as markup.
    """

    _SKIP = {"script", "style", "head", "title", "meta", "link"}
    _BREAK = {"p", "br", "div", "tr", "li", "h1", "h2", "h3", "h4", "h5", "h6"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self._chunks = []
        self._skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in self._SKIP:
            self._skip_depth += 1
        elif tag in self._BREAK:
            self._chunks.append("\n")

    def handle_endtag(self, tag):
        if tag in self._SKIP and self._skip_depth:
            self._skip_depth -= 1
        elif tag in self._BREAK:
            self._chunks.append("\n")

    def handle_data(self, data):
        if not self._skip_depth and data:
            self._chunks.append(data)

    def get_text(self):
        text = "".join(self._chunks)
        text = re.sub(r"[ \t\r\f\v]+", " ", text)
        text = re.sub(r"\n\s*\n\s*", "\n\n", text)
        return text.strip()


def html_to_text(value):
    """Readable plain text from HTML mail."""
    if not value:
        return ""
    parser = _TextExtractor()
    try:
        parser.feed(value)
        parser.close()
    except Exception:
        # Malformed markup must never stop a message being processed.
        return html.unescape(re.sub(r"<[^>]+>", " ", value)).strip()
    return parser.get_text()


def strip_quoted_blocks(text):
    """Remove quoted reply history.

    Cuts at the first quote marker rather than filtering '>' lines throughout:
    everything after "On ... wrote:" belongs to the earlier message, and the
    reply itself is what should be classified.
    """
    if not text:
        return ""

    cut = len(text)
    for pattern in (_QUOTE_INTRO, _OUTLOOK_SEPARATOR):
        match = pattern.search(text)
        if match and match.start() < cut:
            cut = match.start()
    text = text[:cut]

    # Drop any remaining '>' quoted lines and stray forwarded header blocks.
    lines = [ln for ln in text.splitlines() if not ln.lstrip().startswith(">")]
    text = "\n".join(lines)
    text = _OUTLOOK_HEADER_BLOCK.sub("", text)
    return text.strip()


def strip_greeting(text):
    """Remove standalone greeting lines from the beginning of a message."""
    if not text:
        return ""

    lines = text.splitlines()
    index = 0
    while index < len(lines) and not lines[index].strip():
        index += 1
    while index < len(lines) and _GREETING_LINE.match(lines[index]):
        index += 1
        while index < len(lines) and not lines[index].strip():
            index += 1
    if index == 0:
        return text.strip()
    return "\n".join(lines[index:]).strip()


def strip_signature(text):
    """Drop a trailing signature block."""
    if not text:
        return ""

    match = _SIGNATURE_DELIMITER.search(text)
    if match:
        text = text[: match.start()]

    for match in reversed(list(_SIGNOFF.finditer(text))):
        remainder = text[match.end():].strip()
        if not remainder or len(remainder.splitlines()) <= 4:
            return text[: match.start()].strip()
    return text.strip()


def _normalize_readable_whitespace(text):
    value = unicodedata.normalize("NFKC", str(text or ""))
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    lines = [re.sub(r"[ \t\f\v\u00a0]+", " ", line).strip() for line in value.split("\n")]
    return "\n".join(lines).strip()


def clean_readable_body(body_text, body_html=""):
    """Return readable body text without common mail boilerplate."""
    candidates = []
    if body_text:
        candidates.append(str(body_text))
    if body_html:
        html_text = html_to_text(body_html)
        if html_text:
            candidates.append(html_text)

    for source in candidates:
        cleaned = strip_quoted_blocks(source)
        cleaned = strip_greeting(cleaned)
        cleaned = strip_signature(cleaned)
        cleaned = _TRAILING_COURTESY.sub("", cleaned)
        cleaned = _normalize_readable_whitespace(cleaned)
        if cleaned:
            return cleaned
    return ""


def has_meaningful_content(subject, body_text="", body_html=""):
    """Whether a message has a meaningful subject or cleaned body."""
    return bool(normalize_subject(subject) or clean_readable_body(body_text, body_html))


def normalize_body(body_text, body_html=""):
    """The classification input: reply-stripped, signature-stripped, normalised."""
    return normalize_text(clean_readable_body(body_text, body_html))



def body_fingerprint(normalized_body):
    return hashlib.sha256((normalized_body or "").encode("utf-8", "replace")).hexdigest()


def compute_dedupe_key(*, message_id="", from_email="", received_at=None,
                       normalized_subject="", body_hash=""):
    """The NOT NULL de-duplication key behind the unique index.

    A usable Message-ID is authoritative. Without one, fall back to spec 11's
    fingerprint: sender, a received-time window, normalised subject and body
    hash.

    The timestamp is truncated to the minute so that re-fetching the same
    message with a slightly different internal date still collides. It is not
    widened further: two genuinely distinct messages from one sender with an
    identical subject and body inside the same minute are vanishingly rare next
    to the cost of silently discarding real mail.
    """
    if message_id:
        return hashlib.sha256(f"mid:{message_id.strip().lower()}".encode()).hexdigest()

    stamp = received_at.strftime("%Y%m%d%H%M") if received_at else ""
    raw = f"fp:{(from_email or '').strip().lower()}|{stamp}|{normalized_subject}|{body_hash}"
    return hashlib.sha256(raw.encode("utf-8", "replace")).hexdigest()
