"""Database functions that need MariaDB-specific handling."""

from django.db.models import Func, IntegerField


class DateDiff(Func):
    """MariaDB DATEDIFF(a, b) -> integer days between two dates.

    Two Django-native alternatives are wrong here and both were verified against
    MariaDB 11.8 before choosing this:

    * `ExtractDay(a - b)` raises "Extract requires native DurationField database
      support" -- MySQL/MariaDB has no native interval type.
    * Bare `F("a") - F("b")` on DATE columns compiles to numeric subtraction of
      the YYYYMMDD representation, so 2026-10-01 minus 2026-09-30 yields 71
      rather than 1. It looks correct in same-month tests and breaks silently
      across month boundaries.

    DATEDIFF returns a true calendar-day count and is index-friendly.
    """

    function = "DATEDIFF"
    arity = 2
    output_field = IntegerField()
