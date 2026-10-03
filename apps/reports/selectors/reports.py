"""Report selectors (spec 33-36).

All of these consume an already-scoped queryset: report endpoints must not
widen what a user can see (spec 14).
"""

import datetime

from django.db.models import Avg, Count, Q

from apps.bugs.constants import TERMINAL_STATUSES, AGING_BANDS, BugStatus
from apps.bugs.models import BugReopenHistory
from apps.bugs.selectors import base_bug_queryset
from common.db.functions import DateDiff
from common.permissions.scoping import scope_bug_queryset
from common.utils.dates import local_today


def _scoped(user, today=None):
    return scope_bug_queryset(base_bug_queryset(today or local_today()), user)


def _grouped(queryset):
    """Prepare a queryset for values().annotate() aggregation.

    Bug.Meta sets ordering = ["-id"]. Django appends ordering columns to the
    GROUP BY, so a default-ordered queryset groups by (field, id) and returns
    one row per bug instead of one row per group. Calling order_by() with no
    arguments clears the default ordering and restores real grouping.
    """
    return queryset.order_by()


def daily_bug_report(user, report_date=None):
    """Opening + New - Closed = Closing (spec 33).

    "Opening" is reconstructed as: reported before the date, and either never
    closed or closed on/after it.

    Known limitation, accepted for v1: a bug closed, reopened and re-closed
    within the reporting window is counted from its latest closed_date, so the
    opening balance can be off by one for that bug. The exact version replays
    bug_status_history and is deferred.
    """
    report_date = report_date or local_today()
    qs = scope_bug_queryset(
        base_bug_queryset(report_date).model.objects.filter(is_deleted=False), user)

    opening = qs.filter(reported_date__lt=report_date).filter(
        Q(closed_date__isnull=True) | Q(closed_date__gte=report_date)
    ).count()
    new_bugs = qs.filter(reported_date=report_date).count()
    closed = qs.filter(closed_date=report_date).count()
    reopened = BugReopenHistory.objects.filter(
        bug__in=qs.values("id"), reopened_at__date=report_date).count()

    annotated = _scoped(user, report_date)
    return {
        "report_date": report_date.isoformat(),
        "opening_bugs": opening,
        "new_bugs": new_bugs,
        "closed_bugs": closed,
        "closing_bugs": opening + new_bugs - closed,
        "reopened": reopened,
        "overdue": annotated.filter(is_overdue=True).count(),
        "critical": annotated.filter(priority__code="CRITICAL")
                             .exclude(status__in=TERMINAL_STATUSES).count(),
        "update_pending": annotated.filter(is_update_pending=True).count(),
    }


def employee_report(user, today=None):
    """Spec 34. Counts support workload balancing, not individual scoring."""
    from apps.dashboard.selectors import team_workload

    return team_workload(user, today)


def project_report(user, today=None):
    not_terminal = ~Q(status__in=TERMINAL_STATUSES)
    rows = (_grouped(_scoped(user, today))
            .values("project__unique_id", "project__code", "project__name")
            .annotate(
                total=Count("id"),
                open_count=Count("id", filter=not_terminal),
                closed=Count("id", filter=Q(status=BugStatus.CLOSED)),
                overdue=Count("id", filter=Q(is_overdue=True)),
                critical=Count("id", filter=Q(priority__code="CRITICAL") & not_terminal),
            ).order_by("-total"))
    return [{"id": str(r["project__unique_id"]), "code": r["project__code"],
             "name": r["project__name"], "total": r["total"],
             "open": r["open_count"], "closed": r["closed"],
             "overdue": r["overdue"], "critical": r["critical"]} for r in rows]


def module_report(user, today=None, project=None):
    """Spec 35, with drill-down to submodule."""
    qs = _scoped(user, today).filter(module__isnull=False)
    if project:
        qs = qs.filter(project__unique_id=project)
    not_terminal = ~Q(status__in=TERMINAL_STATUSES)
    rows = (_grouped(qs).values("module__unique_id", "module__name", "project__name")
            .annotate(
                total=Count("id"),
                open_count=Count("id", filter=not_terminal),
                closed=Count("id", filter=Q(status=BugStatus.CLOSED)),
            ).order_by("-total"))
    return [{"id": str(r["module__unique_id"]), "name": r["module__name"],
             "project": r["project__name"], "total": r["total"],
             "open": r["open_count"], "closed": r["closed"]} for r in rows]


def submodule_report(user, module, today=None):
    rows = (_grouped(_scoped(user, today))
            .filter(submodule__isnull=False, module__unique_id=module)
            .values("submodule__unique_id", "submodule__name")
            .annotate(total=Count("id")).order_by("-total"))
    return [{"id": str(r["submodule__unique_id"]), "name": r["submodule__name"],
             "total": r["total"]} for r in rows]


def priority_report(user, today=None):
    not_terminal = ~Q(status__in=TERMINAL_STATUSES)
    rows = (_grouped(_scoped(user, today))
            .values("priority__code", "priority__name", "priority__color", "priority__rank")
            .annotate(
                total=Count("id"),
                open_count=Count("id", filter=not_terminal),
                closed=Count("id", filter=Q(status=BugStatus.CLOSED)),
                overdue=Count("id", filter=Q(is_overdue=True)),
            ).order_by("priority__rank"))
    return [{"code": r["priority__code"], "name": r["priority__name"],
             "color": r["priority__color"], "total": r["total"],
             "open": r["open_count"], "closed": r["closed"],
             "overdue": r["overdue"]} for r in rows]


def aging_report(user, today=None):
    qs = _scoped(user, today).filter(~Q(status__in=TERMINAL_STATUSES))
    rows = _grouped(qs).values("aging_band").annotate(count=Count("id"))
    lookup = {r["aging_band"]: r["count"] for r in rows}
    summary = [{"code": c, "label": l, "count": lookup.get(c, 0)} for c, l in AGING_BANDS]
    return {
        "summary": summary,
        "average_age": round(qs.aggregate(a=Avg("age_days"))["a"] or 0, 1),
        "total_open": sum(row["count"] for row in summary),
    }


def overdue_report(user, today=None):
    from apps.bugs.serializers import BugListSerializer

    qs = _scoped(user, today).filter(is_overdue=True).order_by("-overdue_days")
    return {
        "count": qs.count(),
        "total_overdue_days": sum(b.overdue_days for b in qs),
        "bugs": BugListSerializer(qs[:200], many=True).data,
    }


def closure_report(user, date_from=None, date_to=None, today=None):
    today = today or local_today()
    date_to = date_to or today
    date_from = date_from or (today - datetime.timedelta(days=30))
    qs = _scoped(user, today).filter(closed_date__gte=date_from, closed_date__lte=date_to)
    stats = qs.aggregate(
        total=Count("id"),
        avg_closure_days=Avg(DateDiff("closed_date", "reported_date")),
    )
    by_user = (_grouped(qs).filter(closed_by__isnull=False)
               .values("closed_by__unique_id", "closed_by__full_name")
               .annotate(count=Count("id")).order_by("-count"))
    return {
        "date_from": date_from.isoformat(),
        "date_to": date_to.isoformat(),
        "total_closed": stats["total"],
        "avg_closure_days": round(stats["avg_closure_days"] or 0, 1),
        "by_user": [{"id": str(r["closed_by__unique_id"]),
                     "name": r["closed_by__full_name"], "count": r["count"]}
                    for r in by_user],
    }


def root_cause_report(user, today=None, **filters):
    """Spec 36, filterable by project/module/date/developer/priority/severity."""
    qs = _scoped(user, today).filter(root_cause_type__isnull=False)
    if filters.get("project"):
        qs = qs.filter(project__unique_id=filters["project"])
    if filters.get("module"):
        qs = qs.filter(module__unique_id=filters["module"])
    if filters.get("owner"):
        qs = qs.filter(owner__unique_id=filters["owner"])
    if filters.get("priority"):
        qs = qs.filter(priority__code=filters["priority"])
    if filters.get("severity"):
        qs = qs.filter(severity__code=filters["severity"])
    if filters.get("date_from"):
        qs = qs.filter(reported_date__gte=filters["date_from"])
    if filters.get("date_to"):
        qs = qs.filter(reported_date__lte=filters["date_to"])

    rows = (_grouped(qs).values("root_cause_type__code", "root_cause_type__name")
            .annotate(count=Count("id")).order_by("-count"))
    total = sum(r["count"] for r in rows) or 1
    return [{"code": r["root_cause_type__code"], "name": r["root_cause_type__name"],
             "count": r["count"], "percentage": round(r["count"] * 100.0 / total, 1)}
            for r in rows]
