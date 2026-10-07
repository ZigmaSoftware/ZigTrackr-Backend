"""Requester mail is queued durably and delivered outside the ticket request."""

from unittest.mock import patch

from django.core import mail
from django.db import transaction
from django.test import TestCase, override_settings

from apps.tickets.models import OutboundMail
from apps.tickets.services.ack_service import should_acknowledge
from apps.tickets.services.outbound_mail import (
    queue_acknowledgement, queue_assigned_notice, queue_closed_notice,
)
from apps.tickets.tasks import deliver_outbound_mail, recover_outbound_mail
from apps.tickets.tests.factories import make_ticket


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class OutboundMailTests(TestCase):
    def test_no_reply_provider_alias_never_receives_automated_mail(self):
        self.assertFalse(should_acknowledge("google-noreply@google.com"))
        self.assertTrue(should_acknowledge("person@google.com"))

    def test_ack_is_queued_with_stable_thread_id_and_sent_once(self):
        ticket = make_ticket()
        with transaction.atomic():
            job = queue_acknowledgement(ticket=ticket, to_email="sender@example.com",
                                        in_reply_to="<original@example.com>")
        self.assertEqual(mail.outbox, [])
        ticket.refresh_from_db()
        self.assertEqual(ticket.ack_message_id, job.message_id)
        self.assertIsNone(ticket.ack_sent_at)
        self.assertEqual(deliver_outbound_mail(job.pk), "SENT")
        self.assertEqual(deliver_outbound_mail(job.pk), "SENT")
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].extra_headers["Message-ID"], f"<{job.message_id}>")
        ticket.refresh_from_db()
        self.assertIsNotNone(ticket.ack_sent_at)

    def test_assignment_and_close_are_preserved(self):
        ticket = make_ticket(ticket_no="TKT-2610-0001")
        with transaction.atomic():
            assigned = queue_assigned_notice(ticket=ticket)
            closed = queue_closed_notice(ticket=ticket, remarks="Verified")
        self.assertEqual({assigned.kind, closed.kind}, {"ASSIGNED", "CLOSED"})
        deliver_outbound_mail(assigned.pk)
        deliver_outbound_mail(closed.pk)
        self.assertEqual(len(mail.outbox), 2)
        self.assertIn("TKT-2610-0001", mail.outbox[0].body)
        self.assertIn("Verified", mail.outbox[1].body)

    def test_smtp_failure_keeps_a_retryable_job(self):
        ticket = make_ticket()
        with transaction.atomic():
            job = queue_acknowledgement(ticket=ticket, to_email="sender@example.com")
        with patch("django.core.mail.EmailMultiAlternatives.send", side_effect=ConnectionError("offline")):
            self.assertEqual(deliver_outbound_mail(job.pk), "RETRY")
        job.refresh_from_db()
        self.assertEqual(job.status, OutboundMail.Status.RETRY)
        self.assertEqual(job.attempts, 1)
        self.assertIsNotNone(job.next_attempt_at)

    def test_broker_failure_does_not_lose_the_job(self):
        ticket = make_ticket()
        with patch("apps.tickets.tasks.deliver_outbound_mail.apply_async",
                   side_effect=ConnectionError("Redis unavailable")):
            with self.captureOnCommitCallbacks(execute=True):
                with transaction.atomic():
                    job = queue_acknowledgement(ticket=ticket, to_email="sender@example.com")
        job.refresh_from_db()
        self.assertEqual(job.status, OutboundMail.Status.PENDING)

    def test_recovery_republishes_pending_job(self):
        ticket = make_ticket()
        with transaction.atomic():
            job = queue_acknowledgement(ticket=ticket, to_email="sender@example.com")
        with patch("apps.tickets.tasks.deliver_outbound_mail.delay") as publish:
            recover_outbound_mail()
        publish.assert_called_once_with(job.pk)

    def test_repeated_ack_enqueue_is_one_job(self):
        ticket = make_ticket()
        with transaction.atomic():
            first = queue_acknowledgement(ticket=ticket, to_email="sender@example.com")
            second = queue_acknowledgement(ticket=ticket, to_email="sender@example.com")
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(OutboundMail.objects.filter(ticket=ticket).count(), 1)
