"""Date-scoped ticket activity, not a list of each ticket's latest state.

System updates already have lifecycle activities; exclude them to avoid counting
the same action twice. Human/email updates from both ticket domains are included.
All three queries are scoped before aggregation, search, or SQL pagination.
"""

import calendar
from datetime import datetime, time, timedelta

from django.conf import settings
from django.db.models import Case, CharField, Count, F, Q, Value, When
from django.db.models.functions import Cast, Coalesce, NullIf
from django.utils import timezone
from rest_framework import serializers
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from apps.accounts.services.role_labels import role_labels_for_users
from apps.bugs.models import Bug, BugUpdate
from apps.tickets.models import SupportTicket, TicketActivity, TicketUpdate
from common.permissions.require import RequirePermission
from common.permissions.scoping import scope_bug_queryset, scope_ticket_queryset
from common.responses import ok

CATEGORIES = ("unassigned", "assigned", "rectified", "closed", "other")
EVENTS = {
    "unassigned": ("TICKET_RECEIVED", "BUG_CREATED"),
    "assigned": ("TICKET_ASSIGNED", "TICKET_REASSIGNED", "WORK_STARTED",
                 "RETURNED_TO_DEVELOPER", "TICKET_RETURNED_TO_DEVELOPER", "TICKET_REOPENED", "TICKET_PENDING",
                 "TICKET_ON_HOLD", "BUG_STATUS_IN_PROGRESS", "BUG_STATUS_PENDING",
                 "BUG_STATUS_ON_HOLD", "BUG_STATUS_REOPENED", "TEST_FAILED", "TEST_BLOCKED"),
    "rectified": ("TICKET_RECTIFIED", "SERVICE_COMPLETED", "BUG_STATUS_TESTING",
                  "BUG_STATUS_RESOLVED", "TEST_PASSED"),
    "closed": ("TICKET_CLOSED", "BUG_STATUS_CLOSED", "BUG_STATUS_REJECTED", "TICKET_REJECTED"),
}


class DailyParams(serializers.Serializer):
    from_date = serializers.DateField(required=False)
    to_date = serializers.DateField(required=False)
    month = serializers.DateField(required=False)
    category = serializers.ChoiceField(choices=("all", *CATEGORIES), default="all")
    search = serializers.CharField(required=False, allow_blank=True, max_length=200)
    page = serializers.IntegerField(min_value=1, default=1)
    limit = serializers.IntegerField(min_value=1, max_value=100, default=25)

    def validate(self, attrs):
        today = timezone.localdate()
        start = attrs.setdefault("from_date", today)
        end = attrs.setdefault("to_date", start)
        if start > end:
            raise serializers.ValidationError("From date must be on or before To date.")
        if any(not 1900 <= day.year <= 2200 for day in (start, end, attrs.get("month", today))):
            raise serializers.ValidationError("Select a date between 1900 and 2200.")
        return attrs


def category_case(field, mapping):
    return Case(*[When(**{f"{field}__in": values}, then=Value(key))
                  for key, values in mapping.items()], default=Value("other"), output_field=CharField())


def empty_counts():
    return {"all": 0, **dict.fromkeys(CATEGORIES, 0)}


def date_bounds(start, end):
    tz = timezone.get_current_timezone()
    return (timezone.make_aware(datetime.combine(start, time.min), tz),
            timezone.make_aware(datetime.combine(end + timedelta(days=1), time.min), tz))


def sources(user, start, end):
    visible_bugs = scope_bug_queryset(Bug.objects.filter(is_deleted=False), user)
    visible = scope_ticket_queryset(SupportTicket.objects.filter(is_deleted=False), user).filter(
        Q(bug__isnull=True) | Q(bug__in=visible_bugs))
    lower, upper = date_bounds(start, end)
    activities = TicketActivity.objects.filter(
        ticket__in=visible, occurred_at__gte=lower, occurred_at__lt=upper,
    ).annotate(category=category_case("event_type", EVENTS))
    ticket_updates = TicketUpdate.objects.filter(
        ticket__in=visible, created_at__gte=lower, created_at__lt=upper,
    ).exclude(source="SYSTEM").annotate(category=Value("other", output_field=CharField()))
    bug_updates = BugUpdate.objects.filter(
        bug__support_ticket__in=visible, created_at__gte=lower, created_at__lt=upper,
    ).filter(
        # The mail thread service marks requester replies as system-generated
        # (there is no authenticated author). They are real progress updates,
        # not the synthetic notes accompanying status changes.
        Q(is_system_generated=False) | Q(remarks__startswith="Email reply from ")
    ).annotate(category=category_case("status", {
        "unassigned": ("NEW",),
        "assigned": ("ASSIGNED", "IN_PROGRESS", "PENDING", "ON_HOLD", "REOPENED"),
        "rectified": ("TESTING", "RESOLVED"), "closed": ("CLOSED", "REJECTED"),
    }))
    return ((activities, "ticket__", "occurred_at", "description", "actor_user"),
            (ticket_updates, "ticket__", "created_at", "update_text", "created_by_user"),
            (bug_updates, "bug__support_ticket__", "created_at", "update_text", "updated_by"))


def summaries(items, *, start=None):
    result = {} if start else empty_counts()
    for query, _, timestamp, _, _ in items:
        query = query.order_by()
        fields = ["category"]
        if start:
            # MariaDB's named-timezone tables are not always installed. Use
            # Python's zoneinfo boundaries instead of CONVERT_TZ/TruncDate,
            # which silently returns NULL without those tables. This also
            # respects daylight-saving transitions for other deployment zones.
            days = [start + timedelta(days=index) for index in range(42)]
            query = query.annotate(day=Case(*[
                When(**{f"{timestamp}__gte": date_bounds(day, day)[0],
                        f"{timestamp}__lt": date_bounds(day, day)[1]}, then=Value(day.isoformat()))
                for day in days
            ], output_field=CharField()))
            fields.append("day")
        for row in query.values(*fields).annotate(total=Count("pk")):
            counts = result.setdefault(row["day"], empty_counts()) if start else result
            counts[row["category"]] += row["total"]
            counts["all"] += row["total"]
    return result


def user_name(path):
    return Coalesce(NullIf(F(f"{path}__full_name"), Value("")), F(f"{path}__username"), Value(""))


def flat_rows(items, category, search):
    queries = []
    fields = ("kind", "row_id", "ticket_uuid", "reference", "request_title", "ticket_type",
              "project_name", "category", "text", "at", "owner_id_value", "owner_name",
              "actor_name", "priority_name", "priority_color")
    for index, (query, prefix, timestamp, text_field, actor) in enumerate(items):
        if category != "all":
            query = query.filter(category=category)
        if search:
            query = query.filter(Q(**{f"{prefix}title__icontains": search})
                                 | Q(**{f"{prefix}ref_no__icontains": search})
                                 | Q(**{f"{prefix}ticket_no__icontains": search})
                                 | Q(**{f"{text_field}__icontains": search}))
        owner = "owner" if index == 2 else f"{prefix}owner"
        query = query.order_by().annotate(
            kind=Value(("activity", "ticket-update", "bug-update")[index], output_field=CharField()),
            row_id=Cast("pk", CharField()), ticket_uuid=F(f"{prefix}unique_id"),
            reference=Coalesce(NullIf(F(f"{prefix}ticket_no"), Value("")), F(f"{prefix}ref_no")),
            request_title=F(f"{prefix}title"), ticket_type=F(f"{prefix}ticket_type"),
            project_name=Coalesce(F(f"{prefix}project__name"), Value("")),
            text=F(text_field), at=F(timestamp), owner_id_value=F(f"{owner}__pk"),
            owner_name=user_name(owner), actor_name=user_name(actor),
            priority_name=Coalesce(F(f"{prefix}priority__name"), Value("")),
            priority_color=Coalesce(F(f"{prefix}priority__color"), Value("")),
        ).values(*fields)
        queries.append(query)
    return queries[0].union(*queries[1:], all=True).order_by("-at", "kind", "row_id")


class DailyUpdatesView(APIView):
    permission_classes = [IsAuthenticated, RequirePermission("bugs.update.view"),
                          RequirePermission("tickets.ticket.view")]

    def get(self, request):
        params = DailyParams(data=request.query_params)
        params.is_valid(raise_exception=True)
        values = params.validated_data
        items = sources(request.user, values["from_date"], values["to_date"])
        rows = flat_rows(items, values["category"], values.get("search", ""))
        count = rows.count()
        limit = values["limit"]
        offset = (values["page"] - 1) * limit
        results = list(rows[offset:offset + limit])
        roles = role_labels_for_users(row["owner_id_value"] for row in results if row["owner_id_value"])
        for row in results:
            row["owner_role"] = roles.get(row.pop("owner_id_value"), "")
        return ok({"counts": summaries(items), "count": count, "page": values["page"],
                   "total_pages": (count + limit - 1) // limit, "results": results,
                   "today": timezone.localdate(), "timezone": settings.TIME_ZONE})


class DailyUpdatesCalendarView(DailyUpdatesView):
    def get(self, request):
        params = DailyParams(data=request.query_params)
        params.is_valid(raise_exception=True)
        month = params.validated_data.get("month", timezone.localdate()).replace(day=1)
        # Include the adjacent dates visible in the six-week calendar grid.
        start = month - timedelta(days=month.weekday())
        end = start + timedelta(days=41)
        return ok({"days": summaries(sources(request.user, start, end), start=start),
                   "month": month, "today": timezone.localdate(), "timezone": settings.TIME_ZONE,
                   "month_days": calendar.monthrange(month.year, month.month)[1]})
