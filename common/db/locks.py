"""MariaDB advisory locks, used to stop two intake runs overlapping.

Scope note, because the distinction matters: this lock is an efficiency measure,
not the correctness guarantee. Per-message de-duplication is enforced by the
UNIQUE constraint on mail_intake.dedupe_key, which is race-free by construction.
A lock that leaks or is never acquired therefore degrades to "two runs do
redundant work", never to duplicate tickets.

GET_LOCK is deliberately NOT used for per-message dedup. It is session-scoped,
and with CONN_MAX_AGE connection reuse a lock left behind by a crashed process
lingers until the connection recycles, which gives a false sense of mutual
exclusion.
"""

import contextlib
import logging

from django.db import connection

logger = logging.getLogger(__name__)

# MariaDB caps lock names at 64 characters.
MAX_LOCK_NAME_LENGTH = 64


@contextlib.contextmanager
def advisory_lock(name, timeout=0):
    """Hold a named MariaDB lock for the duration of the block.

    Yields True when the lock was acquired and False when another session holds
    it -- the caller decides whether that is an error or simply a reason to skip
    this run. `timeout` is seconds to wait; 0 means fail immediately.

    On a backend without GET_LOCK (SQLite in a future test setup, say) this
    yields True rather than failing, so the lock never becomes the reason a test
    suite cannot run.
    """
    name = name[:MAX_LOCK_NAME_LENGTH]

    if connection.vendor != "mysql":
        logger.debug("advisory_lock: no-op on vendor %s", connection.vendor)
        yield True
        return

    acquired = False
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT GET_LOCK(%s, %s)", [name, timeout])
            row = cursor.fetchone()
        # 1 acquired, 0 timed out, NULL on error.
        acquired = bool(row and row[0] == 1)
        yield acquired
    finally:
        if acquired:
            try:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT RELEASE_LOCK(%s)", [name])
            except Exception:  # pragma: no cover - release must never mask the real error
                logger.exception("Failed to release advisory lock %s", name)
