"""Base querysets for the bug domain."""

from apps.bugs.models import Bug
from apps.bugs.selectors.annotations import with_all_computed

# Every relation the list serializer touches. Without this the bug list issues
# one query per row per relation; assertNumQueries in the tests holds the line.
BUG_LIST_RELATIONS = (
    "project",
    "module",
    "submodule",
    "priority",
    "severity",
    "owner",
    "reported_by",
    "assigned_by",
    "department",
    "site",
    "root_cause_type",
)


def base_bug_queryset(today=None, include_deleted=False):
    qs = Bug.objects.all() if include_deleted else Bug.objects.filter(is_deleted=False)
    return with_all_computed(qs.select_related(*BUG_LIST_RELATIONS), today=today)
