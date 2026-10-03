"""Mail intake list filters (spec 38)."""

import django_filters
from django.db.models import Q

from apps.bugs.filters.bug_filter import UUIDLookupFilter
from apps.mail_intake.constants import MailProcessingStatus
from apps.mail_intake.models import MailIntake
from apps.tickets.constants import TicketType


class MailIntakeFilter(django_filters.FilterSet):
    search = django_filters.CharFilter(method="filter_search")
    received_from = django_filters.DateFilter(field_name="received_at", lookup_expr="date__gte")
    received_to = django_filters.DateFilter(field_name="received_at", lookup_expr="date__lte")
    from_email = django_filters.CharFilter(field_name="from_email", lookup_expr="icontains")

    processing_status = django_filters.MultipleChoiceFilter(
        choices=MailProcessingStatus.choices
    )
    ticket_type = django_filters.MultipleChoiceFilter(
        field_name="linked_ticket__ticket_type", choices=TicketType.choices
    )
    project = UUIDLookupFilter(field_name="linked_ticket__project__unique_id")
    module = UUIDLookupFilter(field_name="linked_ticket__module__unique_id")

    needs_review = django_filters.BooleanFilter(field_name="linked_ticket__needs_review")
    ticket_created = django_filters.BooleanFilter(method="filter_ticket_created")
    has_attachment = django_filters.BooleanFilter(method="filter_has_attachment")

    min_score = django_filters.NumberFilter(
        field_name="linked_ticket__classification_score", lookup_expr="gte"
    )
    max_score = django_filters.NumberFilter(
        field_name="linked_ticket__classification_score", lookup_expr="lte"
    )

    class Meta:
        model = MailIntake
        fields = ["is_duplicate", "is_auto_reply", "is_bounce", "is_thread_reply"]

    def filter_search(self, queryset, name, value):
        value = (value or "").strip()
        if not value:
            return queryset
        return queryset.filter(
            Q(subject__icontains=value)
            | Q(from_email__icontains=value)
            | Q(from_name__icontains=value)
            | Q(body_text__icontains=value)
            | Q(linked_ticket__ticket_no__icontains=value)
        )

    def filter_ticket_created(self, queryset, name, value):
        if value is None:
            return queryset
        return queryset.filter(linked_ticket__isnull=not value)

    def filter_has_attachment(self, queryset, name, value):
        if value is None:
            return queryset
        # distinct(): the attachments join duplicates rows and would corrupt
        # the pagination count.
        return queryset.filter(attachments__isnull=not value).distinct()
