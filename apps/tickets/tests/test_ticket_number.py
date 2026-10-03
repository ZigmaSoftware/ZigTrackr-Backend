"""Ticket number allocation (mirrors apps/bugs/tests/test_bug_number.py)."""

import datetime

from django.test import TestCase, TransactionTestCase

from apps.tickets.services.ticket_number import (
    REF_SEQUENCE_TABLE,
    allocate_number,
    format_ref_no,
    format_ticket_no,
    generate_ref_no,
    generate_ticket_no,
    period_for,
)


class FormatTests(TestCase):
    def test_period_is_yymm(self):
        self.assertEqual(period_for(datetime.date(2026, 9, 18)), "2609")

    def test_format_pads_to_four_digits(self):
        self.assertEqual(format_ticket_no("2609", 7), "TKT-2609-0007")

    def test_ref_format_uses_its_own_prefix(self):
        self.assertEqual(format_ref_no("2609", 7), "REF-2609-0007")


class RefSequenceTests(TestCase):
    """The ref series is independent of the ticket series."""

    def test_ref_and_ticket_counters_do_not_share_numbers(self):
        day = datetime.date(2026, 9, 18)
        first_ref = generate_ref_no(day)
        generate_ticket_no(day)
        second_ref = generate_ref_no(day)

        self.assertEqual(first_ref, "REF-2609-0001")
        # Would be 0003 if the two shared one counter.
        self.assertEqual(second_ref, "REF-2609-0002")

    def test_unknown_sequence_table_is_rejected(self):
        with self.assertRaises(ValueError):
            allocate_number(datetime.date(2026, 9, 18), table="ticket_no_sequence; DROP")

    def test_ref_table_constant_is_accepted(self):
        _, number = allocate_number(datetime.date(2026, 9, 18), table=REF_SEQUENCE_TABLE)
        self.assertEqual(number, 1)

    def test_sequence_increments(self):
        day = datetime.date(2026, 9, 18)
        self.assertEqual(generate_ticket_no(day), "TKT-2609-0001")
        self.assertEqual(generate_ticket_no(day), "TKT-2609-0002")

    def test_sequence_is_per_period(self):
        self.assertEqual(generate_ticket_no(datetime.date(2026, 9, 1)), "TKT-2609-0001")
        self.assertEqual(generate_ticket_no(datetime.date(2026, 10, 1)), "TKT-2610-0001")


class ConcurrencyTests(TransactionTestCase):
    """The reason a counter row is used rather than MAX(ticket_no)+1."""

    def test_concurrent_allocation_produces_no_duplicates(self):
        import threading

        from django.db import connection

        day = datetime.date(2026, 9, 18)
        results = []
        lock = threading.Lock()

        def allocate():
            try:
                for _ in range(20):
                    _, number = allocate_number(day)
                    with lock:
                        results.append(number)
            finally:
                connection.close()

        threads = [threading.Thread(target=allocate) for _ in range(5)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(len(results), 100)
        self.assertEqual(len(set(results)), 100, "allocation produced duplicates")
        self.assertEqual(sorted(results), list(range(1, 101)))
