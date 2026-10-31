from datetime import timedelta
from types import SimpleNamespace

from django.http import QueryDict
from django.test import TestCase
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.test import APIClient

from apps.bugs.tests.factories import make_bug, make_masters
from apps.tickets.constants import TicketStatus
from apps.tickets.filters.ticket_filter import TicketFilter
from apps.tickets.selectors import base_ticket_queryset
from apps.tickets.tests.factories import make_ticket, make_user


class TicketLedgerFilterTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = make_user("ledger-admin", is_superuser=True)
        cls.owner = make_user("ledger-owner")
        cls.assigned = make_ticket(owner=cls.owner, status=TicketStatus.ASSIGNED)
        cls.active = make_ticket(owner=cls.owner, status=TicketStatus.IN_PROGRESS)
        cls.testing = make_ticket(owner=cls.owner, status=TicketStatus.TESTING)
        cls.closed = make_ticket(owner=cls.owner, status=TicketStatus.CLOSED)
        cls.rejected = make_ticket(owner=cls.owner, status=TicketStatus.REJECTED)
        cls.overdue = make_ticket(owner=cls.owner, status=TicketStatus.PENDING, expected_closure_date=timezone.localdate() - timedelta(days=1))
        cls.unassigned = make_ticket()
        cls.project, cls.module, cls.priority, cls.severity = make_masters()
        cls.bug = make_bug(bug_no="BUG-2610-8041", reporter=cls.admin, owner=cls.owner,
                           project=cls.project, priority=cls.priority, severity=cls.severity,
                           status=TicketStatus.TESTING)
        cls.linked = make_ticket(owner=cls.owner, status=TicketStatus.ASSIGNED, bug=cls.bug)

    def filtered(self, **params):
        query = QueryDict("", mutable=True)
        query.update({"submodule": "all", **params})
        request = SimpleNamespace(user=self.admin, query_params=query)
        filters = TicketFilter(query, queryset=base_ticket_queryset(), request=request)
        self.assertTrue(filters.is_valid(), filters.errors)
        return filters.qs

    def test_ledger_scopes_use_linked_bug_status_and_exclude_unassigned(self):
        expected = {
            "all": [self.assigned, self.active, self.testing, self.overdue, self.linked],
            "active": [self.assigned, self.active, self.overdue],
            "verify": [self.testing, self.linked],
            "overdue": [self.overdue],
            "resolved": [self.closed],
        }
        for scope, tickets in expected.items():
            with self.subTest(scope=scope):
                self.assertCountEqual(self.filtered(ledger_scope=scope).values_list("pk", flat=True), [ticket.pk for ticket in tickets])

    def test_normal_all_preset_still_excludes_terminal_records(self):
        self.assertNotIn(self.closed.pk, self.filtered().values_list("pk", flat=True))

    def test_ledger_all_excludes_linked_closed_bug(self):
        self.bug.status = TicketStatus.CLOSED
        self.bug.save(update_fields=["status"])
        self.assertNotIn(self.linked.pk, self.filtered(ledger_scope="all").values_list("pk", flat=True))

    def test_closed_tickets_stay_available_on_closed_page(self):
        client = APIClient()
        client.force_authenticate(user=self.admin)
        response = client.get("/api/v1/tickets/", {"submodule": "all", "ledger_scope": "all"})
        self.assertEqual(response.status_code, 200, response.content)
        self.assertNotIn(str(self.closed.unique_id), [row["id"] for row in response.json()["data"]["results"]])
        response = client.get("/api/v1/tickets/", {"submodule": "closed"})
        self.assertEqual(response.status_code, 200, response.content)
        self.assertIn(str(self.closed.unique_id), [row["id"] for row in response.json()["data"]["results"]])

    def test_scope_keeps_owner_search_and_company_filters(self):
        self.assertEqual(list(self.filtered(ledger_scope="all", owner=str(self.owner.unique_id), search=self.assigned.ref_no).values_list("pk", flat=True)), [self.assigned.pk])
        self.assertFalse(self.filtered(ledger_scope="all", owner=str(self.admin.unique_id)).exists())

    def test_ledger_cannot_bypass_submodule_permission(self):
        query = QueryDict("submodule=all&ledger_scope=resolved")
        request = SimpleNamespace(user=self.owner, query_params=query)
        with self.assertRaises(PermissionDenied):
            TicketFilter(query, queryset=base_ticket_queryset(), request=request).qs

    def test_ledger_requires_all_submodule(self):
        query = QueryDict("ledger_scope=resolved")
        request = SimpleNamespace(user=self.admin, query_params=query)
        with self.assertRaises(ValidationError):
            TicketFilter(query, queryset=base_ticket_queryset(), request=request).qs

    def test_priority_and_due_ordering_use_linked_bug_values(self):
        self.assertEqual(base_ticket_queryset().get(pk=self.linked.pk).ledger_priority_rank, 3)
        self.assertEqual(self.filtered(ledger_scope="all").order_by("-ledger_priority_rank").first().pk, self.linked.pk)
        self.bug.expected_closure_date = timezone.localdate() + timedelta(days=2)
        self.bug.save(update_fields=["expected_closure_date"])
        self.assertEqual(base_ticket_queryset().get(pk=self.linked.pk).effective_expected_closure_date, self.bug.expected_closure_date)

    def test_list_endpoint_returns_resolved_and_orders_by_effective_priority(self):
        client = APIClient()
        client.force_authenticate(user=self.admin)
        response = client.get("/api/v1/tickets/", {"submodule": "all", "ledger_scope": "resolved"})
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual([row["id"] for row in response.json()["data"]["results"]], [str(self.closed.unique_id)])
        response = client.get("/api/v1/tickets/", {"submodule": "all", "ledger_scope": "all", "ordering": "-ledger_priority_rank", "limit": 1})
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["data"]["results"][0]["id"], str(self.linked.unique_id))
