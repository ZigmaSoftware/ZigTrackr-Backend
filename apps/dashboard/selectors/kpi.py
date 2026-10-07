"""Dashboard KPI aggregation (spec 21.2, 32)."""

from django.db.models import Avg, Case, CharField, Count, F, Q, When

from apps.bugs.constants import DAILY_UPDATE_REQUIRED_STATUSES, TERMINAL_STATUSES, BugStatus
from apps.bugs.models import Bug
from apps.bugs.selectors import base_bug_queryset
from apps.tickets.models import SupportTicket
from apps.tickets.constants import TERMINAL_TICKET_STATUSES, TicketStatus
from apps.tickets.selectors import base_ticket_queryset
from common.db.functions import DateDiff
from common.permissions.scoping import scope_bug_queryset, scope_ticket_queryset
from common.utils.dates import local_today


def dashboard_kpis(user, today=None):
    """Every KPI card in one query.

    Count(filter=Q(...)) compiles to COUNT(CASE WHEN ... THEN 1 END), so all
    fifteen figures come from a single table scan rather than fifteen round
    trips. This is the highest-leverage decision on the dashboard, which is the
    most frequently loaded screen in the app.
    """
    today = today or local_today()
    ticket_qs = scope_ticket_queryset(base_ticket_queryset(today), user).annotate(
        # Linked bug state is authoritative, rather than the mirrored status.
        workflow_status=Case(When(bug__isnull=False, then=F("bug__status")),
                             default=F("status"), output_field=CharField()),
    )
    orphan_bugs = scope_bug_queryset(
        base_bug_queryset(today).filter(support_ticket__isnull=True), user
    )
    month_start = today.replace(day=1)
    ticket_not_terminal = ~Q(workflow_status__in=TERMINAL_TICKET_STATUSES)
    bug_not_terminal = ~Q(status__in=TERMINAL_STATUSES)

    tickets = ticket_qs.aggregate(
        total_open=Count("id", filter=ticket_not_terminal),
        new_today=Count("id", filter=Q(created_at__date=today)),
        assigned=Count("id", filter=Q(workflow_status=TicketStatus.ASSIGNED)),
        in_progress=Count("id", filter=Q(workflow_status=TicketStatus.IN_PROGRESS)),
        testing=Count("id", filter=Q(workflow_status=TicketStatus.TESTING)),
        resolved=Count("id", filter=Q(workflow_status__in=(BugStatus.RESOLVED, TicketStatus.COMPLETED))),
        on_hold=Count("id", filter=Q(workflow_status=TicketStatus.ON_HOLD)),
        reopened=Count("id", filter=Q(workflow_status=TicketStatus.REOPENED)),
        critical=Count("id", filter=(Q(priority__code="CRITICAL") | Q(bug__priority__code="CRITICAL")) & ticket_not_terminal),
        high=Count("id", filter=(Q(priority__code="HIGH") | Q(bug__priority__code="HIGH")) & ticket_not_terminal),
        overdue=Count("id", filter=Q(is_overdue=True)),
        unassigned=Count("id", filter=Q(owner__isnull=True, bug__owner__isnull=True) & ticket_not_terminal),
        update_pending=Count(
            "id",
            filter=Q(bug__status__in=DAILY_UPDATE_REQUIRED_STATUSES)
            & (Q(bug__latest_update_date__isnull=True) | Q(bug__latest_update_date__lt=today)),
        ),
        closed_today=Count("id", filter=Q(status=TicketStatus.CLOSED, updated_at__date=today) | Q(bug__closed_date=today)),
        closed_this_month=Count(
            "id", filter=Q(status=TicketStatus.CLOSED, updated_at__date__gte=month_start, updated_at__date__lte=today)
            | Q(bug__closed_date__gte=month_start, bug__closed_date__lte=today)),
    )
    bugs = orphan_bugs.aggregate(
        total_open=Count("id", filter=bug_not_terminal),
        new_today=Count("id", filter=Q(reported_date=today)),
        assigned=Count("id", filter=Q(status=BugStatus.ASSIGNED)),
        in_progress=Count("id", filter=Q(status=BugStatus.IN_PROGRESS)),
        testing=Count("id", filter=Q(status=BugStatus.TESTING)),
        resolved=Count("id", filter=Q(status=BugStatus.RESOLVED)),
        on_hold=Count("id", filter=Q(status=BugStatus.ON_HOLD)),
        reopened=Count("id", filter=Q(status=BugStatus.REOPENED)),
        critical=Count("id", filter=Q(priority__code="CRITICAL") & bug_not_terminal),
        high=Count("id", filter=Q(priority__code="HIGH") & bug_not_terminal),
        overdue=Count("id", filter=Q(is_overdue=True)),
        unassigned=Count("id", filter=Q(owner__isnull=True) & bug_not_terminal),
        update_pending=Count("id", filter=Q(is_update_pending=True)),
        closed_today=Count("id", filter=Q(closed_date=today)),
        closed_this_month=Count(
            "id", filter=Q(closed_date__gte=month_start, closed_date__lte=today)),
    )
    return {key: (tickets.get(key) or 0) + (bugs.get(key) or 0) for key in tickets}


def average_metrics(user, today=None):
    """Average closure time and age (spec 32)."""
    today = today or local_today()
    qs = scope_bug_queryset(base_bug_queryset(today), user)
    closed = qs.filter(closed_date__isnull=False).aggregate(
        avg_closure_days=Avg(DateDiff("closed_date", "reported_date")),
    )
    open_bugs = qs.filter(~Q(status__in=TERMINAL_STATUSES)).aggregate(
        avg_age_days=Avg("age_days"),
    )
    return {
        "avg_closure_days": round(closed["avg_closure_days"] or 0, 1),
        "avg_age_days": round(open_bugs["avg_age_days"] or 0, 1),
    }


def sidebar_counts(user, today=None):
    """Live badge counts for the sidebar (spec 17.6).

    Only the menus the spec calls out get a badge; a counter on every item is
    noise.
    """
    kpis = dashboard_kpis(user, today)
    today = today or local_today()
    qs = scope_bug_queryset(base_bug_queryset(today), user)
    ticket_qs = scope_ticket_queryset(base_ticket_queryset(today), user)
    assigned_to_me = qs.filter(
        owner=user, status__in=[s for s in BugStatus.values if s not in TERMINAL_STATUSES]
    ).count()
    ticket_assigned_to_me = ticket_qs.filter(
        Q(owner=user) | Q(bug__owner=user),
        is_terminal=False,
    ).count()
    visible_bugs = scope_bug_queryset(Bug.objects.all(), user).values("pk")
    chat_unread = scope_ticket_queryset(
        SupportTicket.objects.filter(is_deleted=False, owner__isnull=False), user
    ).filter(
        Q(bug__isnull=True) | Q(bug__pk__in=visible_bugs),
        chat_messages__sender_type="REQUESTER",
        chat_messages__read_at__isnull=True,
        chat_messages__is_deleted=False,
    ).values("pk").distinct().count()
    verification_queue = ticket_qs.filter(
        Q(bug__status__in=("TESTING", "REOPENED"))
        | Q(bug__isnull=True, status__in=("TESTING", "REOPENED"))
    ).count()
    return {
        "assigned_to_me": assigned_to_me + ticket_assigned_to_me,
        "unassigned": kpis["unassigned"],
        "critical": kpis["critical"],
        "overdue": kpis["overdue"],
        "testing": verification_queue,
        "update_pending": kpis["update_pending"],
        "reopened": kpis["reopened"],
        "chat_unread": chat_unread,
    }
