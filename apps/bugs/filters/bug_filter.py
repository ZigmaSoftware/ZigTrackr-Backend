"""Bug list filters (spec 24)."""

import uuid as uuid_module

import django_filters
from django.db.models import Q

from apps.bugs.constants import AGING_BANDS, BugStatus, Environment
from apps.bugs.models import Bug
from common.utils.dates import local_today


class UUIDLookupFilter(django_filters.CharFilter):
    """Filter on a UUID column without 400-ing on a malformed value.

    django-filter passes the raw string to the UUID field, so a stale bookmark
    or a hand-edited querystring raises a validation error and the whole list
    request fails. A filter value that cannot match anything should simply
    match nothing -- an empty list is a correct answer, an error page is not.
    """

    def filter(self, qs, value):
        value = (value or "").strip()
        if not value:
            return qs
        try:
            parsed = uuid_module.UUID(value)
        except (ValueError, AttributeError, TypeError):
            return qs.none()
        return qs.filter(**{self.field_name: parsed})


class BugFilter(django_filters.FilterSet):
    """All spec 24 filters over one queryset.

    The computed filters (is_overdue, is_update_pending, aging_band, min_age)
    work because the annotations are real SQL expressions rather than Python
    properties -- see apps/bugs/selectors/annotations.py.
    """

    search = django_filters.CharFilter(method="filter_search")
    project = UUIDLookupFilter(field_name="project__unique_id")
    module = UUIDLookupFilter(field_name="module__unique_id")
    submodule = UUIDLookupFilter(field_name="submodule__unique_id")
    department = UUIDLookupFilter(field_name="department__unique_id")
    site = UUIDLookupFilter(field_name="site__unique_id")

    status = django_filters.MultipleChoiceFilter(choices=BugStatus.choices)
    exclude_status = django_filters.MultipleChoiceFilter(
        choices=BugStatus.choices, field_name="status", exclude=True)
    environment = django_filters.MultipleChoiceFilter(choices=Environment.choices)

    priority = django_filters.CharFilter(field_name="priority__code")
    severity = django_filters.CharFilter(field_name="severity__code")
    root_cause_type = django_filters.CharFilter(field_name="root_cause_type__code")

    # "me" resolves to the signed-in user. Presets such as My Bugs and
    # Assigned to Me are static config, so they cannot embed a per-user UUID;
    # the sentinel keeps them shareable URLs that mean the right thing for
    # whoever opens them.
    owner = django_filters.CharFilter(method="filter_owner")
    reporter = django_filters.CharFilter(method="filter_reporter")
    unassigned = django_filters.BooleanFilter(
        field_name="owner", lookup_expr="isnull")

    reported_from = django_filters.DateFilter(field_name="reported_date", lookup_expr="gte")
    reported_to = django_filters.DateFilter(field_name="reported_date", lookup_expr="lte")
    closure_from = django_filters.DateFilter(
        field_name="expected_closure_date", lookup_expr="gte")
    closure_to = django_filters.DateFilter(
        field_name="expected_closure_date", lookup_expr="lte")
    closed_from = django_filters.DateFilter(field_name="closed_date", lookup_expr="gte")
    closed_to = django_filters.DateFilter(field_name="closed_date", lookup_expr="lte")

    # ---- COMPUTED (spec 11, 12, 13) ----
    is_overdue = django_filters.BooleanFilter(field_name="is_overdue")
    is_update_pending = django_filters.BooleanFilter(field_name="is_update_pending")
    aging_band = django_filters.ChoiceFilter(field_name="aging_band", choices=AGING_BANDS)
    min_age = django_filters.NumberFilter(field_name="age_days", lookup_expr="gte")
    max_age = django_filters.NumberFilter(field_name="age_days", lookup_expr="lte")
    reopened = django_filters.BooleanFilter(method="filter_reopened")
    updated_today = django_filters.BooleanFilter(method="filter_updated_today")

    class Meta:
        model = Bug
        fields = []

    def _resolve_user(self, value):
        """Return a UUID, or None when the value cannot identify a user.

        Returning None (rather than raising) lets the caller answer with an
        empty result set instead of a 400, which is the sane outcome for a
        stale or hand-edited URL.
        """
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

    def filter_owner(self, queryset, name, value):
        resolved = self._resolve_user(value)
        if resolved is None:
            return queryset.none()
        return queryset.filter(owner__unique_id=resolved)

    def filter_reporter(self, queryset, name, value):
        resolved = self._resolve_user(value)
        if resolved is None:
            return queryset.none()
        return queryset.filter(reported_by__unique_id=resolved)

    def filter_search(self, queryset, name, value):
        """Global search across the spec 24 fields.

        Leading-wildcard LIKE cannot use a B-tree index. Acceptable at the scale
        of an internal tracker; a FULLTEXT index on (title, description) is the
        documented upgrade path if it ever becomes slow.
        """
        value = value.strip()
        if not value:
            return queryset
        return queryset.filter(
            Q(bug_no__icontains=value)
            | Q(title__icontains=value)
            | Q(description__icontains=value)
            | Q(project__name__icontains=value)
            | Q(module__name__icontains=value)
            | Q(owner__full_name__icontains=value)
            | Q(owner__username__icontains=value)
        )

    def filter_reopened(self, queryset, name, value):
        if value is True:
            return queryset.filter(reopen_count__gt=0)
        if value is False:
            return queryset.filter(reopen_count=0)
        return queryset

    def filter_updated_today(self, queryset, name, value):
        if value is True:
            return queryset.filter(updates__update_date=local_today()).distinct()
        if value is False:
            return queryset.exclude(updates__update_date=local_today()).distinct()
        return queryset
