"""Pagination with page-number support on top of limit/offset."""

from collections import OrderedDict

from rest_framework.pagination import LimitOffsetPagination
from rest_framework.response import Response
from rest_framework.settings import api_settings


class LimitOffsetWithPage(LimitOffsetPagination):
    """Limit/offset pagination that also accepts ?page= and reports total_pages.

    The house envelope is {count, next, previous, page, total_pages, results}.
    Accepting both ?offset= and ?page= lets tables paginate by page number while
    infinite-scroll style callers keep using offsets.
    """

    default_limit = api_settings.PAGE_SIZE or 25
    max_limit = 5000
    page_query_param = "page"

    def paginate_queryset(self, queryset, request, view=None):
        limit = self.get_limit(request)
        page = request.query_params.get(self.page_query_param)
        if page and self.page_query_param not in ("offset",):
            try:
                page_number = max(1, int(page))
                request.query_params._mutable = True
                request.query_params["offset"] = str((page_number - 1) * (limit or self.default_limit))
                request.query_params._mutable = False
            except (TypeError, ValueError):
                pass
        return super().paginate_queryset(queryset, request, view)

    def get_paginated_response(self, data):
        limit = self.limit or self.default_limit
        total_pages = (self.count + limit - 1) // limit if limit else 1
        current_page = (self.offset // limit) + 1 if limit else 1
        return Response(OrderedDict([
            ("count", self.count),
            ("next", self.get_next_link()),
            ("previous", self.get_previous_link()),
            ("page", current_page),
            ("total_pages", total_pages),
            ("results", data),
        ]))
