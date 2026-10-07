"""Unified ticket list filters."""

import uuid as uuid_module
from datetime import datetime, time, timedelta

import django_filters
from django.db.models import Q
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError

from apps.bugs.constants import BugStatus
from apps.tickets.constants import TERMINAL_TICKET_STATUSES, TicketSource, TicketStatus, TicketType
from apps.tickets.models import SupportTicket
from common.permissions.require import has_permission


class UUIDLookupFilter(django_filters.CharFilter):
    def filter(self, qs, value):
        value = (value or "").strip()
        if not value:
            return qs
        try:
            parsed = uuid_module.UUID(value)
        except (ValueError, AttributeError, TypeError):
            return qs.none()
        return qs.filter(**{self.field_name: parsed})


class TicketFilter(django_filters.FilterSet):
    submodule = django_filters.CharFilter(method="filter_submodule")
    search = django_filters.CharFilter(method="filter_search")
    ticket_type = django_filters.MultipleChoiceFilter(choices=TicketType.choices)
    source = django_filters.MultipleChoiceFilter(choices=TicketSource.choices)
    status = django_filters.CharFilter(method="filter_status")
    verification_queue = django_filters.BooleanFilter(method="filter_verification_queue")
    project = UUIDLookupFilter(field_name="project__unique_id")
    module = UUIDLookupFilter(field_name="module__unique_id")
    owner = django_filters.CharFilter(method="filter_owner")
    assigned = django_filters.BooleanFilter(method="filter_assigned")
    reassignable = django_filters.BooleanFilter(method="filter_reassignable")
    unassigned = django_filters.BooleanFilter(method="filter_unassigned")
    needs_review = django_filters.BooleanFilter(field_name="needs_review")
    priority = django_filters.CharFilter(method="filter_priority")
    critical = django_filters.BooleanFilter(method="filter_critical")
    overdue = django_filters.BooleanFilter(field_name="is_overdue")
    terminal = django_filters.BooleanFilter(method="filter_terminal")
    created_from = django_filters.DateFilter(field_name="created_at", lookup_expr="date__gte")
    created_to = django_filters.DateFilter(field_name="created_at", lookup_expr="date__lte")
    received_date = django_filters.DateFilter(method="filter_received_date")
    expected_from = django_filters.DateFilter(field_name="effective_expected_closure_date", lookup_expr="gte")
    expected_to = django_filters.DateFilter(field_name="effective_expected_closure_date", lookup_expr="lte")

    class Meta:
        model = SupportTicket
        fields = []

    def filter_submodule(self, queryset, name, value):
        """Check the actual page gate and enforce its preset on the server.

        Generic scoped lists remain available to chat and existing API clients.
        A submodule grant is a page/action entitlement, not a new ownership tier.
        """
        presets = {
            "all": {"assigned": True, "terminal": False},
            "unassigned": {"unassigned": True, "terminal": False},
            "reassign": {"reassignable": True},
            "bugs": {"ticket_type": TicketType.BUG},
            "services": {"ticket_type": TicketType.SERVICE_REQUEST},
            "access": {"ticket_type": TicketType.ACCESS_REQUEST},
            "critical": {"critical": True, "terminal": False},
            "overdue": {"is_overdue": True, "terminal": False},
            "testing": {"verification_queue": True},
            "closed": {"terminal": True},
        }
        if value not in presets:
            raise ValidationError({"submodule": ["Choose a valid ticket submodule."]})
        if not has_permission(getattr(self.request, "user", None), f"tickets.{value}.access"):
            raise PermissionDenied("You do not have access to this ticket submodule.")
        for field, selected in presets[value].items():
            method = getattr(self, f"filter_{field}", None)
            queryset = method(queryset, field, selected) if method else queryset.filter(**{field: selected})
        return queryset

    def _resolve_user(self, value):
        value = (value or "").strip()
        if not value:
            return None
        if value.lower() == "me":
            user = getattr(self.request, "user", None)
            return getattr(user, "unique_id", None) if user else None
        try:
            return uuid_module.UUID(value)
        except (ValueError, AttributeError, TypeError):
            return None

    def filter_search(self, queryset, name, value):
        value = (value or "").strip()
        if not value:
            return queryset
        return queryset.filter(
            Q(ticket_no__icontains=value)
            | Q(ref_no__icontains=value)
            | Q(bug__bug_no__icontains=value)
            | Q(title__icontains=value)
            | Q(description__icontains=value)
            | Q(reported_by_email__icontains=value)
            | Q(reported_by_name__icontains=value)
            | Q(reported_by__full_name__icontains=value)
            | Q(project__name__icontains=value)
            | Q(module__name__icontains=value)
            | Q(bug__project__name__icontains=value)
            | Q(bug__module__name__icontains=value)
            | Q(owner__full_name__icontains=value)
            | Q(owner__username__icontains=value)
            | Q(bug__owner__full_name__icontains=value)
            | Q(bug__owner__username__icontains=value)
        )

    def filter_received_date(self, queryset, name, value):
        """Match the date shown in the unassigned queue's Mailed date column."""
        if not value:
            return queryset
        zone = timezone.get_current_timezone()
        start = timezone.make_aware(datetime.combine(value, time.min), zone)
        end = timezone.make_aware(datetime.combine(value + timedelta(days=1), time.min), zone)
        return queryset.filter(
            Q(original_mail_received_at__gte=start, original_mail_received_at__lt=end)
            | Q(original_mail_received_at__isnull=True, created_at__gte=start, created_at__lt=end)
        )

    def filter_status(self, queryset, name, value):
        values = [v for v in self.request.query_params.getlist(name) if v]
        if not values and value:
            values = [value]
        if not values:
            return queryset
        return queryset.filter(
            Q(bug__isnull=True, status__in=values) | Q(bug__status__in=values)
        )

    def filter_verification_queue(self, queryset, name, value):
        if not value:
            return queryset
        statuses = (TicketStatus.TESTING, TicketStatus.REOPENED)
        return queryset.filter(
            Q(bug__status__in=statuses) | Q(bug__isnull=True, status__in=statuses)
        )

    def filter_owner(self, queryset, name, value):
        resolved = self._resolve_user(value)
        if resolved is None:
            return queryset.none()
        return queryset.filter(Q(owner__unique_id=resolved) | Q(bug__owner__unique_id=resolved))

    def filter_assigned(self, queryset, name, value):
        assigned_q = Q(owner__isnull=False) | Q(bug__owner__isnull=False)
        return queryset.filter(assigned_q) if value else queryset.exclude(assigned_q)

    def filter_reassignable(self, queryset, name, value):
        if not value:
            return queryset
        from apps.tickets.services.policy import reassignable_ticket_queryset

        return reassignable_ticket_queryset(queryset, getattr(self.request, "user", None))

    def filter_unassigned(self, queryset, name, value):
        unassigned_q = Q(owner__isnull=True) & Q(bug__owner__isnull=True)
        return queryset.filter(unassigned_q) if value else queryset.exclude(unassigned_q)

    def filter_priority(self, queryset, name, value):
        if not value:
            return queryset
        return queryset.filter(Q(priority__code=value) | Q(bug__priority__code=value))

    def filter_critical(self, queryset, name, value):
        critical_q = Q(priority__code="CRITICAL") | Q(bug__priority__code="CRITICAL")
        return queryset.filter(critical_q) if value else queryset.exclude(critical_q)

    def filter_terminal(self, queryset, name, value):
        terminal_q = Q(status__in=TERMINAL_TICKET_STATUSES) | Q(
            bug__status__in=(BugStatus.CLOSED, BugStatus.REJECTED)
        )
        return queryset.filter(terminal_q) if value else queryset.exclude(terminal_q)
