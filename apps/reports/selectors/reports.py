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


def daily_bug_report(user, report_date=None, request=None):
    """Opening + New - Closed = Closing (spec 33).

    "Opening" is reconstructed as: reported before the date, and either never
    closed or closed on/after it.

    Known limitation, accepted for v1: a bug closed, reopened and re-closed
    within the reporting window is counted from its latest closed_date, so the
    opening balance can be off by one for that bug. The exact version replays
    bug_status_history and is deferred.
    """
    from apps.bugs.serializers import BugListSerializer

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

    # Bug detail list: new on that day plus still-open bugs visible on that day.
    bug_qs = annotated.filter(
        Q(reported_date=report_date)
        | Q(closed_date=report_date)
        | (Q(reported_date__lte=report_date) & (Q(closed_date__isnull=True) | Q(closed_date__gte=report_date)))
    ).order_by("-is_overdue", "-age_days")[:100]
    ctx = {"request": request} if request else {}

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
        "bugs": BugListSerializer(bug_qs, many=True, context=ctx).data,
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


def company_report(user, today=None):
    """Bug totals per company (spec 35, company tier).

    Bug.company is nullable -- bugs predating the multi-company hierarchy, and
    any raised outside one, have none. Those are grouped under a single
    "Unassigned" row rather than dropped, so the totals still reconcile with
    the other reports.
    """
    not_terminal = ~Q(status__in=TERMINAL_STATUSES)
    rows = (_grouped(_scoped(user, today))
            .values("company__unique_id", "company__code", "company__name")
            .annotate(
                total=Count("id"),
                open_count=Count("id", filter=not_terminal),
                closed=Count("id", filter=Q(status=BugStatus.CLOSED)),
                overdue=Count("id", filter=Q(is_overdue=True)),
                critical=Count("id", filter=Q(priority__code="CRITICAL") & not_terminal),
            ).order_by("-total"))
    return [{"id": str(r["company__unique_id"]) if r["company__unique_id"] else "",
             "code": r["company__code"] or "",
             "name": r["company__name"] or "Unassigned",
             "total": r["total"], "open": r["open_count"], "closed": r["closed"],
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


# The timeline lists real bugs rather than a count per band, so it needs the
# rows themselves. Capped because this is a triage view: nobody routes the
# 500th oldest bug from a report page, and an unbounded list would make the
# response grow without limit.
AGING_TIMELINE_LIMIT = 150


def aging_report(user, today=None):
    from apps.bugs.serializers import BugListSerializer

    qs = _scoped(user, today).filter(~Q(status__in=TERMINAL_STATUSES))
    rows = _grouped(qs).values("aging_band").annotate(count=Count("id"))
    lookup = {r["aging_band"]: r["count"] for r in rows}
    summary = [{"code": c, "label": l, "count": lookup.get(c, 0)} for c, l in AGING_BANDS]
    total = sum(row["count"] for row in summary)

    # Oldest first: the whole point of an aging view is what has waited
    # longest, so the top of the list is where attention is needed.
    oldest = qs.order_by("-age_days", "reported_date")[:AGING_TIMELINE_LIMIT]
    return {
        "summary": summary,
        "average_age": round(qs.aggregate(a=Avg("age_days"))["a"] or 0, 1),
        "total_open": total,
        "bugs": BugListSerializer(oldest, many=True).data,
        # Lets the client say "showing 150 of 320" rather than silently
        # truncating.
        "shown": min(total, AGING_TIMELINE_LIMIT),
    }


def overdue_report(user, today=None):
    from apps.bugs.serializers import BugListSerializer

    qs = _scoped(user, today).filter(is_overdue=True).order_by("-overdue_days")
    return {
        "count": qs.count(),
        "total_overdue_days": sum(b.overdue_days for b in qs),
        "bugs": BugListSerializer(qs[:200], many=True).data,
    }


def closure_report(user, date_from=None, date_to=None, today=None, request=None):
    from apps.bugs.serializers import BugListSerializer

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
    by_project = (_grouped(qs).filter(project__isnull=False)
                  .values("project__unique_id", "project__name")
                  .annotate(count=Count("id")).order_by("-count"))

    ctx = {"request": request} if request else {}
    return {
        "date_from": date_from.isoformat(),
        "date_to": date_to.isoformat(),
        "total_closed": stats["total"],
        "avg_closure_days": round(stats["avg_closure_days"] or 0, 1),
        "by_user": [{"id": str(r["closed_by__unique_id"]),
                     "name": r["closed_by__full_name"], "count": r["count"]}
                    for r in by_user],
        "by_project": [{"id": str(r["project__unique_id"]),
                        "name": r["project__name"], "count": r["count"]}
                       for r in by_project],
        "bugs": BugListSerializer(qs.order_by("-closed_date")[:200], many=True, context=ctx).data,
    }


def root_cause_report(user, today=None, **filters):
    """Spec 36, filterable by project/module/date/developer/priority/severity.

    Includes bugs that have free-text root_cause but no root_cause_type FK set —
    these are grouped under an "Unclassified" row so they are visible in the report
    rather than silently omitted. Bugs with neither field are excluded.
    """
    # Bugs that have either a typed root cause category OR at least free-text root cause
    qs = _scoped(user, today).filter(
        Q(root_cause_type__isnull=False) | Q(root_cause__isnull=False) & ~Q(root_cause="")
    )
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

    result = []
    for r in rows:
        code = r["root_cause_type__code"]
        name = r["root_cause_type__name"]
        result.append({
            "code": code or "UNCLASSIFIED",
            "name": name or "Unclassified",
            "count": r["count"],
        })

    total = sum(r["count"] for r in result) or 1
    for r in result:
        r["percentage"] = round(r["count"] * 100.0 / total, 1)
    return result
