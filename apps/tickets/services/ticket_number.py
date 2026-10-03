"""Ticket number generation: TKT-YYMM-NNNN (spec 12).

A deliberate near-copy of apps/bugs/services/bug_number.py rather than a shared
generic. The reasoning there -- why SELECT MAX(...)+1 produces duplicates under
concurrency, and why a per-period counter row with INSERT ... ON DUPLICATE KEY
UPDATE serialises allocations safely -- is worth reading at the point of use, and
a parameterised version would need table-name and prefix injection that makes the
raw SQL harder to follow. Extract a common helper if a third sequence appears.
"""

from django.db import connection, transaction

TICKET_NO_PREFIX = "TKT"
REF_NO_PREFIX = "REF"
SEQUENCE_TABLE = "ticket_number_sequence"
REF_SEQUENCE_TABLE = "ticket_ref_sequence"


def period_for(date_value):
    """YYMM string, e.g. 2026-09-18 -> '2609'."""
    return date_value.strftime("%y%m")


@transaction.atomic
def allocate_number(date_value, table=SEQUENCE_TABLE):
    """Reserve and return the next integer for the given date's period.

    `table` is interpolated, not parameterised, because a table name cannot be a
    bind parameter. Only the two module-level constants are ever passed.
    """
    if table not in (SEQUENCE_TABLE, REF_SEQUENCE_TABLE):
        raise ValueError(f"Unknown sequence table: {table!r}")

    period = period_for(date_value)
    with connection.cursor() as cursor:
        cursor.execute(
            f"INSERT INTO {table} (period, last_number, updated_at) "
            "VALUES (%s, 1, NOW()) "
            "ON DUPLICATE KEY UPDATE last_number = last_number + 1, updated_at = NOW()",
            [period],
        )
        cursor.execute(
            f"SELECT last_number FROM {table} WHERE period = %s", [period]
        )
        row = cursor.fetchone()
    return period, int(row[0])


def format_ticket_no(period, number):
    return f"{TICKET_NO_PREFIX}-{period}-{number:04d}"


def format_ref_no(period, number):
    return f"{REF_NO_PREFIX}-{period}-{number:04d}"


def generate_ticket_no(date_value):
    """Return the next ticket number for `date_value`'s period.

    Call inside the transaction that creates the ticket. Gaps are acceptable --
    the number identifies a ticket, it does not count them.
    """
    period, number = allocate_number(date_value)
    return format_ticket_no(period, number)


def generate_ref_no(date_value):
    """Return the next intake reference, REF-YYMM-NNNN.

    Every ticket gets one at creation. The TKT number is minted later, when the
    ticket is reviewed and routed to an owner, so an unrouted request is never
    given a number that implies it was accepted. The ref is what the requester's
    acknowledgement quotes, so it must exist from the first moment and must
    never be reused -- hence its own sequence rather than sharing the TKT
    counter, which would leave visible gaps in both.
    """
    period, number = allocate_number(date_value, table=REF_SEQUENCE_TABLE)
    return format_ref_no(period, number)
