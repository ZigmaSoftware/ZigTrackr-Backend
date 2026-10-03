"""Low-cardinality timing and SQL counts for slow ticket write actions."""

import logging
import re
import time

from django.db import connection

logger = logging.getLogger(__name__)

_TICKET_ACTION = re.compile(r"^/api/v1/tickets/[^/]+/(assign|review|close|work-transition)/$")
_BUG_CLOSE = re.compile(r"^/api/v1/bugs/[^/]+/close/$")


class TicketActionMetricsMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.method != "POST":
            return self.get_response(request)
        match = _TICKET_ACTION.match(request.path)
        action = (f"ticket.{match.group(1)}" if match else
                  "ticket.create" if request.path == "/api/v1/tickets/" else
                  "bug.close" if _BUG_CLOSE.match(request.path) else None)
        if action is None:
            return self.get_response(request)

        count = 0
        db_ms = 0.0

        def count_query(execute, sql, params, many, context):
            nonlocal count, db_ms
            started = time.perf_counter()
            try:
                return execute(sql, params, many, context)
            finally:
                count += 1
                db_ms += (time.perf_counter() - started) * 1000

        started = time.perf_counter()
        response = None
        try:
            with connection.execute_wrapper(count_query):
                response = self.get_response(request)
            return response
        finally:
            logger.info("ticket_action action=%s status=%s duration_ms=%.1f sql_count=%s sql_ms=%.1f",
                        action, getattr(response, "status_code", 500),
                        (time.perf_counter() - started) * 1000, count, db_ms)
