"""IMAP transport (spec 4, 45).

Transport only: this module talks to the mailbox and knows nothing about
tickets, classification or the database. That separation is what lets everything
below it be tested without a mail server.
"""

import contextlib
import imaplib
import logging
import ssl
from typing import NamedTuple

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

logger = logging.getLogger(__name__)

FETCH_PARTS = "(BODY.PEEK[])"


class MailboxConfig(NamedTuple):
    host: str
    port: int
    use_ssl: bool
    username: str
    password: str
    folder: str
    address: str

    def __repr__(self):
        # NamedTuple's default repr prints every field, which would put the app
        # password into any logger.exception stack trace. Masking it here is the
        # difference between a leaked credential and a redacted one.
        return (
            f"MailboxConfig(host={self.host!r}, port={self.port!r}, "
            f"use_ssl={self.use_ssl!r}, username={self.username!r}, "
            f"password='***', folder={self.folder!r}, address={self.address!r})"
        )

    __str__ = __repr__


def load_mailbox_config():
    """Read mailbox settings, failing loudly when intake is on but unconfigured."""
    username = getattr(settings, "MAIL_INTAKE_USERNAME", "") or ""
    password = getattr(settings, "MAIL_INTAKE_APP_PASSWORD", "") or ""
    host = getattr(settings, "MAIL_INTAKE_IMAP_HOST", "") or ""

    missing = [
        name
        for name, value in (
            ("MAIL_INTAKE_IMAP_HOST", host),
            ("MAIL_INTAKE_USERNAME", username),
            ("MAIL_INTAKE_APP_PASSWORD", password),
        )
        if not value
    ]
    if missing:
        raise ImproperlyConfigured(
            "Mail intake is enabled but these settings are empty: " + ", ".join(missing)
        )

    return MailboxConfig(
        host=host,
        port=int(getattr(settings, "MAIL_INTAKE_IMAP_PORT", 993)),
        use_ssl=bool(getattr(settings, "MAIL_INTAKE_USE_SSL", True)),
        username=username,
        password=password,
        folder=getattr(settings, "MAIL_INTAKE_FOLDER", "INBOX") or "INBOX",
        address=getattr(settings, "MAIL_INTAKE_EMAIL", "") or username,
    )


@contextlib.contextmanager
def imap_connection(config):
    """Yield an authenticated IMAP connection, always logging out afterwards."""
    connection = None
    try:
        if config.use_ssl:
            # An explicit default context: it verifies certificates and checks
            # the hostname. Relying on imaplib's implicit behaviour here is
            # version-dependent, and spec 45 requires verification.
            context = ssl.create_default_context()
            connection = imaplib.IMAP4_SSL(config.host, config.port, ssl_context=context)
        else:
            connection = imaplib.IMAP4(config.host, config.port)
            connection.starttls(ssl.create_default_context())

        connection.login(config.username, config.password)
        connection.select(config.folder)
        yield connection

    finally:
        if connection is not None:
            for close in (connection.close, connection.logout):
                try:
                    close()
                except Exception:  # pragma: no cover - teardown must not mask errors
                    pass


def search_uids(connection, *, unseen_only=True, since=None):
    """Return message UIDs matching the search criteria."""
    criteria = []
    if unseen_only:
        criteria.append("UNSEEN")
    if since is not None:
        criteria += ["SINCE", since.strftime("%d-%b-%Y")]
    if not criteria:
        criteria = ["ALL"]

    status, data = connection.uid("SEARCH", None, *criteria)
    if status != "OK":
        raise imaplib.IMAP4.error(f"IMAP SEARCH failed: {status}")

    if not data or not data[0]:
        return []
    return data[0].decode().split()


def fetch_raw(connection, uid):
    """Fetch one message's raw bytes."""
    status, data = connection.uid("FETCH", uid, FETCH_PARTS)
    if status != "OK" or not data or not data[0]:
        raise imaplib.IMAP4.error(f"IMAP FETCH failed for uid {uid}: {status}")

    for part in data:
        if isinstance(part, tuple) and len(part) > 1:
            return part[1]
    raise imaplib.IMAP4.error(f"No message body returned for uid {uid}")


def mark_seen(connection, uid):
    """Flag a message as read so the next UNSEEN search skips it.

    Called only once registration has committed: from that point the message
    lives in our database and the retry sweep owns it. Marking earlier loses
    messages on a crash; marking later re-presents ones already held.
    """
    try:
        connection.uid("STORE", uid, "+FLAGS", "(\\Seen)")
    except Exception:
        logger.warning("Could not mark uid %s as seen", uid, exc_info=True)


def check_connection():
    """Connectivity probe for the health check and for setup troubleshooting."""
    config = load_mailbox_config()
    with imap_connection(config) as connection:
        status, _ = connection.noop()
        return status == "OK"
