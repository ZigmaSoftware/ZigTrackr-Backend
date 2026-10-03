"""Temporary versus permanent failure (spec 44).

The distinction decides whether a message is retried or parked, so it is made in
one place rather than at each call site.
"""

import socket
import ssl

from django.db import DatabaseError, InterfaceError, OperationalError

from apps.mail_intake.constants import (
    PERMANENT_ERROR_CODES,
    TEMPORARY_ERROR_CODES,
    MailErrorCode,
)


def classify_error(exc):
    """Return (error_code, is_temporary) for an exception raised while processing.

    Note IntegrityError is deliberately absent: a unique-key clash is the
    "another worker won the race" path handled in duplicate_service, not a
    failure to report.
    """
    import imaplib

    if isinstance(exc, (imaplib.IMAP4.error, socket.timeout, ssl.SSLError, ConnectionError)):
        return MailErrorCode.IMAP_ERROR, True

    if isinstance(exc, (OperationalError, InterfaceError)):
        return MailErrorCode.DB_ERROR, True

    if isinstance(exc, DatabaseError):
        return MailErrorCode.DB_ERROR, True

    if isinstance(exc, OSError):
        # Covers storage failures; IMAP socket errors are caught above.
        return MailErrorCode.STORAGE_ERROR, True

    if isinstance(exc, (UnicodeError, ValueError, TypeError, KeyError, IndexError)):
        return MailErrorCode.PARSE_ERROR, False

    return MailErrorCode.UNEXPECTED_ERROR, True


def is_permanent(error_code):
    return error_code in PERMANENT_ERROR_CODES


def is_temporary(error_code):
    return error_code in TEMPORARY_ERROR_CODES


def should_retry(*, error_code, attempts, max_attempts):
    """Whether the sweep should pick this message up again."""
    if is_permanent(error_code):
        return False
    return attempts < max_attempts
