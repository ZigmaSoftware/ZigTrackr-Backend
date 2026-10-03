"""Dashboard chart aggregates (spec 21.3, 22).

Each function answers one management question; spec 22 says not to render
charts for decoration.
"""

from django.db.models import Count, Q

from apps.bugs.constants import TERMINAL_STATUSES, AGING_BANDS, BugStatus
from apps.bugs.selectors import base_bug_queryset
from common.permissions.scoping import scope_bug_queryset
from common.utils.dates import local_today


def _scoped(user, today=None):
    """Scoped queryset, with the model's default ordering cleared.

    Every consumer here aggregates with values().annotate(). Django appends
    ordering columns to the GROUP BY, and Bug.Meta orders by -id, so leaving
    the default ordering in place would group by id and return one row per bug.
    """
    qs = scope_bug_queryset(base_bug_queryset(today or local_today()), user)
    return qs.order_by()


def bugs_by_status(user, today=None):
    rows = _scoped(user, today).values("status").annotate(count=Count("id"))
    lookup = {r["status"]: r["count"] for r in rows}
    return [
        {"code": s, "label": BugStatus(s).label, "count": lookup.get(s, 0)}
        for s in BugStatus.values
    ]


def bugs_by_priority(user, today=None):
    rows = (_scoped(user, today)
            .filter(~Q(status__in=TERMINAL_STATUSES))
            .values("priority__code", "priority__name", "priority__color", "priority__rank")
            .annotate(count=Count("id")).order_by("priority__rank"))
    return [{"code": r["priority__code"], "label": r["priority__name"],
             "color": r["priority__color"], "count": r["count"]} for r in rows]


def bugs_by_severity(user, today=None):
    rows = (_scoped(user, today)
            .filter(~Q(status__in=TERMINAL_STATUSES))
            .values("severity__code", "severity__name", "severity__color", "severity__rank")
            .annotate(count=Count("id")).order_by("severity__rank"))
    return [{"code": r["severity__code"], "label": r["severity__name"],
             "color": r["severity__color"], "count": r["count"]} for r in rows]


def bugs_by_module(user, today=None, limit=10):
    rows = (_scoped(user, today)
            .filter(module__isnull=False)
            .values("module__unique_id", "module__name", "project__name")
            .annotate(count=Count("id")).order_by("-count")[:limit])
    return [{"id": str(r["module__unique_id"]), "label": r["module__name"],
             "project": r["project__name"], "count": r["count"]} for r in rows]


def bugs_by_aging_band(user, today=None):
    rows = (_scoped(user, today)
            .filter(~Q(status__in=TERMINAL_STATUSES))
            .values("aging_band").annotate(count=Count("id")))
    lookup = {r["aging_band"]: r["count"] for r in rows}
    return [{"code": code, "label": label, "count": lookup.get(code, 0)}
            for code, label in AGING_BANDS]


def team_workload(user, today=None):
    """Bugs per owner, split by status (spec 21.3, 34)."""
    not_terminal = ~Q(status__in=TERMINAL_STATUSES)
    rows = (_scoped(user, today)
            .filter(owner__isnull=False)
            .values("owner__unique_id", "owner__full_name", "owner__username")
            .annotate(
                assigned=Count("id", filter=Q(status=BugStatus.ASSIGNED)),
                in_progress=Count("id", filter=Q(status=BugStatus.IN_PROGRESS)),
                testing=Count("id", filter=Q(status=BugStatus.TESTING)),
                on_hold=Count("id", filter=Q(status=BugStatus.ON_HOLD)),
                closed=Count("id", filter=Q(status=BugStatus.CLOSED)),
                overdue=Count("id", filter=Q(is_overdue=True)),
                update_pending=Count("id", filter=Q(is_update_pending=True)),
                critical=Count("id", filter=Q(priority__code="CRITICAL") & not_terminal),
                total_open=Count("id", filter=not_terminal),
            ).order_by("-total_open"))
    return [{
        "id": str(r["owner__unique_id"]),
        "name": r["owner__full_name"] or r["owner__username"],
        "assigned": r["assigned"], "in_progress": r["in_progress"],
        "testing": r["testing"], "on_hold": r["on_hold"], "closed": r["closed"],
        "overdue": r["overdue"], "update_pending": r["update_pending"],
        "critical": r["critical"], "total_open": r["total_open"],
    } for r in rows]


def root_cause_distribution(user, today=None):
    """Spec 36: RCA percentages."""
    rows = (_scoped(user, today)
            .filter(root_cause_type__isnull=False)
            .values("root_cause_type__code", "root_cause_type__name")
            .annotate(count=Count("id")).order_by("-count"))
    total = sum(r["count"] for r in rows) or 1
    return [{"code": r["root_cause_type__code"], "label": r["root_cause_type__name"],
             "count": r["count"], "percentage": round(r["count"] * 100.0 / total, 1)}
            for r in rows]


def closure_trend(user, days=30, today=None):
    """Closed-per-day for the trend chart."""
    import datetime

    today = today or local_today()
    start = today - datetime.timedelta(days=days - 1)
    rows = (_scoped(user, today)
            .filter(closed_date__gte=start, closed_date__lte=today)
            .values("closed_date").annotate(count=Count("id")).order_by("closed_date"))
    lookup = {r["closed_date"]: r["count"] for r in rows}

    opened = (_scoped(user, today)
              .filter(reported_date__gte=start, reported_date__lte=today)
              .values("reported_date").annotate(count=Count("id")))
    opened_lookup = {r["reported_date"]: r["count"] for r in opened}

    out = []
    for offset in range(days):
        day = start + datetime.timedelta(days=offset)
        out.append({"date": day.isoformat(),
                    "opened": opened_lookup.get(day, 0),
                    "closed": lookup.get(day, 0)})
    return out
