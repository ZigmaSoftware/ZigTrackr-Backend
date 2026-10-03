"""Dynamic annotations: aging, overdue, update-pending (spec 11, 12, 13).

Spec 12 and 13 are explicit that overdue days and age must never be stored.
They are therefore SQL annotations rather than Python properties -- a property
cannot appear in a WHERE or ORDER BY, and the bug list must both filter and
sort on these (spec 23 lists Age as a column, spec 24 lists Overdue and Update
Pending as filters).

Every function takes `today` so a single request computes one consistent
"today" across all of its cards; a request spanning midnight would otherwise
report figures that disagree with each other.
"""

from django.db.models import (
    BooleanField,
    Case,
    CharField,
    DateField,
    F,
    IntegerField,
    Q,
    Value,
    When,
)

from apps.bugs.constants import DAILY_UPDATE_REQUIRED_STATUSES, TERMINAL_STATUSES
from common.db.functions import DateDiff
from common.utils.dates import local_today


def with_aging(queryset, today=None):
    """age_days = (closed_date or today) - reported_date, plus the spec 13 band.

    A closed bug freezes its age at closure. An age that kept growing after
    closure would make historical aging reports meaningless.
    """
    today = today or local_today()
    return queryset.annotate(
        age_end_date=Case(
            When(status__in=TERMINAL_STATUSES, closed_date__isnull=False, then=F("closed_date")),
            default=Value(today),
            output_field=DateField(),
        ),
    ).annotate(
        age_days=DateDiff(F("age_end_date"), F("reported_date")),
    ).annotate(
        aging_band=Case(
            When(age_days__lte=2, then=Value("NORMAL")),
            When(age_days__lte=5, then=Value("ATTENTION")),
            When(age_days__lte=10, then=Value("WARNING")),
            default=Value("CRITICAL"),
            output_field=CharField(),
        ),
    )


def with_overdue(queryset, today=None):
    """is_overdue / overdue_days per spec 12.

    Overdue means expected_closure_date < today AND the bug is not finished.
    Rejected counts as finished alongside Closed: a rejected bug is not "late".
    """
    today = today or local_today()
    overdue_q = (
        Q(expected_closure_date__isnull=False)
        & Q(expected_closure_date__lt=today)
        & ~Q(status__in=TERMINAL_STATUSES)
    )
    return queryset.annotate(
        is_overdue=Case(
            When(overdue_q, then=Value(True)),
            default=Value(False),
            output_field=BooleanField(),
        ),
        overdue_days=Case(
            When(overdue_q, then=DateDiff(Value(today), F("expected_closure_date"))),
            default=Value(0),
            output_field=IntegerField(),
        ),
    )


def with_update_pending(queryset, today=None):
    """Flag active bugs with no update today (spec 11).

    Reads the denormalised Bug.latest_update_date rather than running a
    correlated NOT EXISTS against bug_updates for every candidate row. Spec 10
    explicitly sanctions keeping the latest update timestamp on the bug row
    "for fast list/report loading", and this turns the dashboard's most
    frequent query into an index range scan on idx_bug_update_pending.

    The invariant that makes it safe: every BugUpdate is written through
    update_service.add_update(), which refreshes latest_update_date in the same
    transaction. The verify_latest_update_fields command checks for drift.
    """
    today = today or local_today()
    pending_q = (
        Q(status__in=DAILY_UPDATE_REQUIRED_STATUSES)
        & (Q(latest_update_date__isnull=True) | Q(latest_update_date__lt=today))
    )
    return queryset.annotate(
        is_update_pending=Case(
            When(pending_q, then=Value(True)),
            default=Value(False),
            output_field=BooleanField(),
        ),
        days_since_update=Case(
            When(latest_update_date__isnull=False,
                 then=DateDiff(Value(today), F("latest_update_date"))),
            default=DateDiff(Value(today), F("reported_date")),
            output_field=IntegerField(),
        ),
    )


def with_all_computed(queryset, today=None):
    today = today or local_today()
    return with_update_pending(with_overdue(with_aging(queryset, today), today), today)
