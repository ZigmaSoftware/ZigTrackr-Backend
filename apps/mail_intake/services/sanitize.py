"""Sanitising untrusted email HTML for display (spec 10.1, 45).

This is a security boundary, not a formatting helper. The input is
attacker-controlled markup from an unauthenticated external sender, and the
audience is an authenticated Admin session.

Four independent layers protect that render, and this module is only the first:

  1. The UI defaults to body_text. HTML is an explicit toggle.
  2. THIS MODULE: sanitise server-side at ingestion, store the result separately,
     and never serialise raw body_html to any API response.
  3. The client renders inside <iframe sandbox="" srcdoc=...> -- an opaque
     origin with scripting disabled, so a sanitiser bypass still cannot execute.
  4. Remote images are neutralised here; the viewer opts in to loading them.

Server-side rather than in the browser because a client-only sanitiser protects
only the one client that runs it: anything consuming the API directly gets the
raw payload. Doing it once at ingestion also means the cost is per message
rather than per view.
"""

import logging
import re

import nh3

logger = logging.getLogger(__name__)

# Deliberately small. Every tag here is inert; anything that can execute, load
# or submit is absent -- script, style, iframe, object, embed, form, input.
ALLOWED_TAGS = {
    "a", "b", "blockquote", "br", "code", "div", "em", "h1", "h2", "h3", "h4",
    "h5", "h6", "hr", "i", "img", "li", "ol", "p", "pre", "s", "small", "span",
    "strong", "sub", "sup", "table", "tbody", "td", "tfoot", "th", "thead",
    "tr", "u", "ul",
}

ALLOWED_ATTRIBUTES = {
    "a": {"href", "title"},
    # src is allowed only because remote URLs are rewritten to
    # data-blocked-src before nh3 runs, so the only src that survives is cid:,
    # which resolves through the permission-gated attachment view.
    "img": {"alt", "title", "width", "height", "src", "data-blocked-src"},
    "td": {"colspan", "rowspan"},
    "th": {"colspan", "rowspan"},
    # No "style" anywhere: CSS can position an overlay over the surrounding UI,
    # and url() reintroduces remote loading.
}

# mailto and cid are safe; javascript:, data: and vbscript: are not.
ALLOWED_URL_SCHEMES = {"http", "https", "mailto", "cid"}

_IMG_SRC = re.compile(r"""<img\b([^>]*?)\ssrc\s*=\s*(["'])(.*?)\2""", flags=re.IGNORECASE | re.DOTALL)


def _block_remote_images(html):
    """Rewrite img src to a data attribute so nothing loads on render.

    A remote <img> in support mail is a tracking pixel: it confirms to the
    sender exactly which administrator opened the message and when, and it
    leaks the office IP address. The viewer can opt in per message.
    """
    if not html:
        return "", 0

    blocked = 0

    def replace(match):
        nonlocal blocked
        attrs, quote, src = match.group(1), match.group(2), match.group(3)
        scheme = src.split(":", 1)[0].lower() if ":" in src else ""
        # cid: refers to an attachment already stored with the message; it is
        # resolved through the permission-gated download view, not the network.
        if scheme == "cid":
            return match.group(0)
        blocked += 1
        return f'<img{attrs} data-blocked-src={quote}{src}{quote}'

    return _IMG_SRC.sub(replace, html), blocked


def sanitize_email_html(html):
    """Return (safe_html, blocked_image_count).

    Never raises: a message whose HTML cannot be cleaned falls back to no HTML
    at all, which is always safe to render.
    """
    if not html:
        return "", 0

    try:
        rewritten, blocked = _block_remote_images(html)
        cleaned = nh3.clean(
            rewritten,
            tags=ALLOWED_TAGS,
            attributes={k: set(v) for k, v in ALLOWED_ATTRIBUTES.items()},
            url_schemes=ALLOWED_URL_SCHEMES,
            link_rel="noopener noreferrer nofollow",
            strip_comments=True,
        )
        return cleaned, blocked
    except Exception:
        logger.exception("Failed to sanitise email HTML; suppressing it entirely")
        return "", 0
