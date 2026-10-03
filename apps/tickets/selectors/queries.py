"""Common SupportTicket query helpers for unified ticket lists."""

from django.db.models import (
    BooleanField,
    Case,
    DateTimeField,
    F,
    IntegerField,
    OuterRef,
    Prefetch,
    Q,
    Subquery,
    Value,
    When,
)
from django.db.models.functions import Coalesce

from apps.bugs.constants import TERMINAL_STATUSES as BUG_TERMINAL_STATUSES
from apps.mail_intake.models import MailIntake
from apps.tickets.constants import TERMINAL_TICKET_STATUSES
from apps.tickets.models import SupportTicket, TicketUpdate
from common.db.functions import DateDiff
from common.utils.dates import local_today


TICKET_LIST_RELATIONS = (
    "project",
    "module",
    "submodule",
    "priority",
    "owner",
    "reported_by",
    "confirmed_by",
    "bug",
    "bug__project",
    "bug__module",
    "bug__priority",
    "bug__owner",
    "bug__reported_by",
)


def with_ticket_computed(queryset, today=None):
    """Annotate dynamic list fields without storing them."""
    today = today or local_today()
    latest_ticket_update = (
        TicketUpdate.objects
        .filter(ticket=OuterRef("pk"))
        .order_by("-created_at")
        .values("created_at")[:1]
    )
    original_mail_received_at = (
        MailIntake.objects.filter(linked_ticket_id=OuterRef("pk"), is_thread_reply=False)
        .order_by("id").values("received_at")[:1]
    )
    terminal_q = Q(status__in=TERMINAL_TICKET_STATUSES) | Q(bug__status__in=BUG_TERMINAL_STATUSES)
    overdue_q = (
        Q(expected_closure_date__isnull=False)
        & Q(expected_closure_date__lt=today)
        & ~terminal_q
    ) | (
        Q(bug__expected_closure_date__isnull=False)
        & Q(bug__expected_closure_date__lt=today)
        & ~Q(bug__status__in=BUG_TERMINAL_STATUSES)
    )
    return queryset.annotate(
        original_mail_received_at=Subquery(original_mail_received_at),
        effective_expected_closure_date=Coalesce(
            "bug__expected_closure_date", "expected_closure_date"
        ),
        effective_last_update_at=Coalesce(
            "bug__latest_update_at",
            Subquery(latest_ticket_update, output_field=DateTimeField()),
            "updated_at",
        ),
        age_days=DateDiff(Value(today), F("created_at")),
        is_overdue=Case(
            When(overdue_q, then=Value(True)),
            default=Value(False),
            output_field=BooleanField(),
        ),
        overdue_days=Case(
            When(
                overdue_q,
                then=DateDiff(Value(today), Coalesce(
                    "bug__expected_closure_date", "expected_closure_date"
                )),
            ),
            default=Value(0),
            output_field=IntegerField(),
        ),
        is_terminal=Case(
            When(terminal_q, then=Value(True)),
            default=Value(False),
            output_field=BooleanField(),
        ),
    )


def base_ticket_queryset(today=None, include_deleted=False):
    qs = SupportTicket.objects.all() if include_deleted else SupportTicket.objects.filter(is_deleted=False)
    return with_ticket_computed(
        qs.select_related(*TICKET_LIST_RELATIONS).prefetch_related(
            Prefetch(
                "mails",
                queryset=MailIntake.objects.filter(is_thread_reply=False).order_by("id"),
                to_attr="_original_mails",
            )
        ),
        today=today,
    )
