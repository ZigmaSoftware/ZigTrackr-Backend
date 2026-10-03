"""Bug number generation tests (spec 5)."""

import datetime
import threading

from django.test import TestCase, TransactionTestCase

from apps.bugs.services.bug_number import format_bug_no, generate_bug_no, period_for


class BugNumberFormatTests(TestCase):
    def test_period_is_yymm(self):
        self.assertEqual(period_for(datetime.date(2026, 9, 14)), "2609")
        self.assertEqual(period_for(datetime.date(2026, 12, 1)), "2612")
        self.assertEqual(period_for(datetime.date(2027, 1, 31)), "2701")

    def test_format_pads_to_four_digits(self):
        self.assertEqual(format_bug_no("2609", 1), "BUG-2609-0001")
        self.assertEqual(format_bug_no("2609", 42), "BUG-2609-0042")
        self.assertEqual(format_bug_no("2609", 9999), "BUG-2609-9999")

    def test_sequence_increments(self):
        d = datetime.date(2026, 9, 14)
        self.assertEqual(generate_bug_no(d), "BUG-2609-0001")
        self.assertEqual(generate_bug_no(d), "BUG-2609-0002")
        self.assertEqual(generate_bug_no(d), "BUG-2609-0003")

    def test_sequence_is_per_period(self):
        self.assertEqual(generate_bug_no(datetime.date(2026, 9, 30)), "BUG-2609-0001")
        # A new month starts its own counter rather than continuing the old one.
        self.assertEqual(generate_bug_no(datetime.date(2026, 10, 1)), "BUG-2610-0001")
        self.assertEqual(generate_bug_no(datetime.date(2026, 9, 30)), "BUG-2609-0002")


class BugNumberConcurrencyTests(TransactionTestCase):
    """The failure this design exists to prevent.

    TransactionTestCase (not TestCase) because real threads need real
    committed transactions -- TestCase wraps everything in one rolled-back
    transaction that worker threads cannot see.
    """

    def test_concurrent_allocation_produces_no_duplicates(self):
        from django.db import connection

        date_value = datetime.date(2026, 9, 14)
        workers, per_worker = 8, 15
        results = []
        errors = []
        lock = threading.Lock()

        def allocate():
            try:
                local = []
                for _ in range(per_worker):
                    local.append(generate_bug_no(date_value))
                with lock:
                    results.extend(local)
            except Exception as exc:  # pragma: no cover - surfaced via assertion
                with lock:
                    errors.append(repr(exc))
            finally:
                connection.close()

        threads = [threading.Thread(target=allocate) for _ in range(workers)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(errors, [], f"Allocation raised: {errors}")
        expected_total = workers * per_worker
        self.assertEqual(len(results), expected_total)
        self.assertEqual(
            len(set(results)), expected_total,
            "Duplicate bug numbers were issued under concurrent allocation.",
        )
        numbers = sorted(int(r.split("-")[-1]) for r in results)
        self.assertEqual(numbers, list(range(1, expected_total + 1)))
