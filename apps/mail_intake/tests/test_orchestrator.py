"""End-to-end intake pipeline (spec 56 scenarios, ticket-first invariants)."""

from django.core import mail as django_mail
from django.test import TestCase, override_settings

from apps.bugs.models import Bug
from apps.classification.models import ClassificationAudit, ClassificationRule
from apps.mail_intake.constants import MailErrorCode, MailProcessingStatus
from apps.mail_intake.models import MailIntake, MailProcessingHistory
from apps.mail_intake.services.intake_orchestrator import (
    IntakeRunResult,
    _continue_processing,
    process_parsed_mail,
)
from apps.mail_intake.tests.factories import make_mail_intake, make_parsed
from apps.tickets.constants import TicketStatus, TicketType
from apps.tickets.models import OutboundMail, SupportTicket
from apps.tickets.tasks import deliver_outbound_mail

SEED_RULES = [
    ("BUG", "SUBJECT_PREFIX", "[BUG]", 100),
    ("SERVICE_REQUEST", "SUBJECT_PREFIX", "[SERVICE]", 100),
    ("ACCESS_REQUEST", "SUBJECT_PREFIX", "[ACCESS]", 100),
    ("BUG", "EXACT_PHRASE", "not working", 85),
    ("BUG", "KEYWORD", "bug", 90),
    ("BUG", "KEYWORD", "error", 80),
    ("ACCESS_REQUEST", "EXACT_PHRASE", "provide access", 100),
    ("ACCESS_REQUEST", "KEYWORD", "permission", 90),
    ("SERVICE_REQUEST", "EXACT_PHRASE", "create login", 90),
    ("SERVICE_REQUEST", "EXACT_PHRASE", "reset password", 100),
]


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class IntakePipelineTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        for ticket_type, rule_type, pattern, score in SEED_RULES:
            ClassificationRule.objects.create(
                ticket_type=ticket_type, rule_type=rule_type,
                pattern=pattern, score=score, is_system=True,
            )

    def setUp(self):
        django_mail.outbox = []

    # ---- Ticket-first invariants ----

    def test_tagged_email_creates_bug_and_linked_ticket(self):
        """An explicit bug email is visible in the normal Bug workflow."""
        process_parsed_mail(make_parsed(subject="[BUG] Save button not working"))
        self.assertEqual(SupportTicket.objects.count(), 1)
        self.assertEqual(Bug.objects.count(), 1)
        ticket = SupportTicket.objects.get()
        self.assertIsNotNone(ticket.bug_id)
        self.assertEqual(ticket.bug.title, "Save button not working")
        self.assertEqual(ticket.bug.description, "Test body")
        self.assertEqual(ticket.original_mail.message_id, "msg-1@example.com")

    def test_reported_by_is_system_user_and_sender_is_preserved(self):
        process_parsed_mail(make_parsed(sender="sadham@zigmaglobal.in"))
        ticket = SupportTicket.objects.get()
        self.assertEqual(ticket.reported_by.username, "mail.intake")
        self.assertEqual(ticket.reported_by_email, "sadham@zigmaglobal.in")
        self.assertFalse(ticket.reported_by.has_usable_password())

    def test_boilerplate_only_mail_is_skipped(self):
        result = IntakeRunResult()
        mail = process_parsed_mail(
            make_parsed(subject="", body="Hi Team,\n\nRegards,\nSupport"),
            result=result,
        )
        self.assertIsNone(mail)
        self.assertEqual(result.empty_skipped, 1)
        self.assertEqual(MailIntake.objects.count(), 0)
        self.assertEqual(SupportTicket.objects.count(), 0)

    def test_cleaned_body_is_used_for_ticket_description(self):
        process_parsed_mail(
            make_parsed(
                subject="[BUG] Save button issue",
                body="Hi Team,\n\nThe save button fails. Thank you.\n\nRegards,\nSupport",
            )
        )
        ticket = SupportTicket.objects.get()
        self.assertEqual(ticket.description, "The save button fails.")
        self.assertEqual(MailIntake.objects.get().body_text.splitlines()[0], "Hi Team,")

    def test_retry_cannot_turn_boilerplate_only_mail_into_ticket(self):
        mail = make_mail_intake(subject="", body_text="Hi Team,\n\nRegards,\nSupport")
        result = IntakeRunResult()
        _continue_processing(mail, parsed=None, result=result)
        mail.refresh_from_db()
        self.assertEqual(mail.processing_status, MailProcessingStatus.REJECTED)
        self.assertEqual(mail.last_error_code, MailErrorCode.EMPTY_CONTENT)
        self.assertEqual(SupportTicket.objects.count(), 0)

    def test_promotional_mail_is_skipped(self):
        result = IntakeRunResult()
        mail = process_parsed_mail(
            make_parsed(subject="Newsletter: July offers"),
            result=result,
        )
        self.assertIsNone(mail)
        self.assertEqual(result.promotional_skipped, 1)
        self.assertEqual(MailIntake.objects.count(), 0)

    def test_no_reply_mail_is_skipped(self):
        result = IntakeRunResult()
        mail = process_parsed_mail(
            make_parsed(sender="no-reply@vendor.example", subject="System update"),
            result=result,
        )
        self.assertIsNone(mail)
        self.assertEqual(result.no_reply_skipped, 1)
        self.assertEqual(MailIntake.objects.count(), 0)

    # ---- Spec 56 scenarios ----

    def test_scenario_1_tagged_bug_email(self):
        process_parsed_mail(
            make_parsed(
                subject="[BUG] User Creation form not working",
                body="Save button is not working while creating a new user.",
            )
        )
        ticket = SupportTicket.objects.get()
        self.assertEqual(ticket.ticket_type, TicketType.BUG)
        self.assertEqual(ticket.classification_score, 100)
        self.assertFalse(ticket.needs_review)
        # Intake issues a reference only. The TKT number is minted when the
        # ticket is reviewed and routed to an owner.
        self.assertTrue(ticket.ref_no.startswith("REF-"))
        self.assertIsNone(ticket.ticket_no)

    def test_scenario_2_natural_language_bug_goes_to_review(self):
        process_parsed_mail(
            make_parsed(
                subject="User creation issue",
                body="Hi team, there have bug in user creation form page reclify it.",
            )
        )
        ticket = SupportTicket.objects.get()
        self.assertEqual(ticket.ticket_type, TicketType.BUG)
        self.assertTrue(ticket.needs_review)
        self.assertEqual(ticket.status, TicketStatus.NEEDS_REVIEW)

    def test_scenario_3_service_request(self):
        process_parsed_mail(make_parsed(subject="Please create login for new employee"))
        ticket = SupportTicket.objects.get()
        self.assertEqual(ticket.ticket_type, TicketType.SERVICE_REQUEST)
        self.assertTrue(ticket.needs_review)
        self.assertEqual(Bug.objects.count(), 0)

    def test_explicit_service_request_creates_actionable_ticket(self):
        process_parsed_mail(make_parsed(subject="[SERVICE] Create login for new employee"))
        ticket = SupportTicket.objects.get()
        self.assertEqual(ticket.ticket_type, TicketType.SERVICE_REQUEST)
        self.assertFalse(ticket.needs_review)
        self.assertEqual(ticket.status, TicketStatus.NEW)
        self.assertEqual(ticket.title, "Create login for new employee")
        self.assertEqual(Bug.objects.count(), 0)

    def test_scenario_4_access_request_awaits_approval(self):
        """Classification must never equal approval (spec 29)."""
        process_parsed_mail(make_parsed(subject="[ACCESS] Provide Purchase Amendment access"))
        ticket = SupportTicket.objects.get()
        self.assertEqual(ticket.ticket_type, TicketType.ACCESS_REQUEST)
        self.assertFalse(ticket.needs_review)
        self.assertEqual(ticket.status, TicketStatus.PENDING_APPROVAL)

    def test_scenario_5_unknown_is_retained_for_review(self):
        """Valid mail is never discarded merely because classification failed."""
        process_parsed_mail(make_parsed(subject="Please check this", body="Need support."))
        ticket = SupportTicket.objects.get()
        self.assertEqual(ticket.ticket_type, TicketType.UNKNOWN)
        self.assertTrue(ticket.needs_review)
        self.assertEqual(MailIntake.objects.count(), 1)

    def test_scenario_6_duplicate_message_id_creates_one_ticket(self):
        parsed = make_parsed(subject="[BUG] Duplicate test", message_id="<dupe@x.com>")
        process_parsed_mail(parsed)
        process_parsed_mail(parsed)
        self.assertEqual(MailIntake.objects.count(), 1)
        self.assertEqual(SupportTicket.objects.count(), 1)

    def test_scenario_7_reply_updates_the_same_ticket(self):
        process_parsed_mail(
            make_parsed(subject="[BUG] Login fails", message_id="<first@x.com>")
        )
        ticket = SupportTicket.objects.get()

        process_parsed_mail(
            make_parsed(
                subject=f"Re: [{ticket.reference}] Login fails",
                body="Still failing today.",
                message_id="<reply@x.com>",
            )
        )
        self.assertEqual(SupportTicket.objects.count(), 1)
        self.assertEqual(MailIntake.objects.count(), 2)
        reply = MailIntake.objects.get(message_id="reply@x.com")
        self.assertEqual(reply.processing_status, MailProcessingStatus.THREAD_UPDATE)
        self.assertEqual(reply.linked_ticket_id, ticket.pk)
        self.assertEqual(ticket.bug.updates.count(), 1)

    def test_reply_via_in_reply_to_on_our_acknowledgement(self):
        """The reply points at OUR ack's Message-ID, not the user's original."""
        process_parsed_mail(
            make_parsed(subject="[BUG] Report screen blank", message_id="<orig@x.com>")
        )
        ticket = SupportTicket.objects.get()
        self.assertTrue(ticket.ack_message_id, "acknowledgement should have been recorded")

        process_parsed_mail(
            make_parsed(
                subject="Re: Report screen blank",  # no ticket number quoted
                message_id="<reply2@x.com>",
                in_reply_to=f"<{ticket.ack_message_id}>",
            )
        )
        self.assertEqual(SupportTicket.objects.count(), 1)
        self.assertEqual(
            MailIntake.objects.get(message_id="reply2@x.com").linked_ticket_id, ticket.pk
        )

    # ---- Rejection paths ----

    def test_auto_reply_creates_no_ticket(self):
        process_parsed_mail(make_parsed(subject="Out of Office: away"))
        self.assertEqual(SupportTicket.objects.count(), 0)
        mail = MailIntake.objects.get()
        self.assertEqual(mail.processing_status, MailProcessingStatus.REJECTED)
        self.assertEqual(mail.last_error_code, MailErrorCode.AUTO_REPLY)
        self.assertTrue(mail.is_auto_reply)

    def test_bounce_creates_no_ticket(self):
        process_parsed_mail(make_parsed(sender="mailer-daemon@googlemail.com"))
        self.assertEqual(SupportTicket.objects.count(), 0)
        self.assertTrue(MailIntake.objects.get().is_bounce)

    def test_rejected_mail_is_still_retained(self):
        process_parsed_mail(make_parsed(subject="Automatic reply: received"))
        self.assertEqual(MailIntake.objects.count(), 1)

    # ---- History and audit ----

    def test_every_transition_is_recorded(self):
        process_parsed_mail(make_parsed(subject="[BUG] History check"))
        mail = MailIntake.objects.get()
        statuses = list(
            MailProcessingHistory.objects.filter(mail=mail)
            .order_by("id").values_list("to_status", flat=True)
        )
        self.assertEqual(statuses[0], MailProcessingStatus.RECEIVED)
        self.assertIn(MailProcessingStatus.VALIDATING, statuses)
        self.assertIn(MailProcessingStatus.TICKET_CREATED, statuses)

    def test_processing_history_is_immutable(self):
        from common.exceptions.domain import ImmutableRecordError

        process_parsed_mail(make_parsed(subject="[BUG] Immutable check"))
        row = MailProcessingHistory.objects.first()
        row.remarks = "tampered"
        with self.assertRaises(ImmutableRecordError):
            row.save()

    def test_classification_audit_captured_from_day_one(self):
        process_parsed_mail(make_parsed(subject="[BUG] Audit check"))
        audit = ClassificationAudit.objects.get()
        self.assertEqual(audit.rule_predicted_type, TicketType.BUG)
        self.assertEqual(audit.human_final_type, "")
        self.assertTrue(audit.matched_rules)

    # ---- Acknowledgement ----

    def test_acknowledgement_sent_with_ticket_number_in_subject(self):
        process_parsed_mail(make_parsed(subject="[BUG] Ack check"))
        ticket = SupportTicket.objects.get()
        job = OutboundMail.objects.get(ticket=ticket, kind="ACK")
        self.assertEqual(len(django_mail.outbox), 0)
        deliver_outbound_mail(job.pk)
        self.assertEqual(len(django_mail.outbox), 1)
        self.assertIn(ticket.reference, django_mail.outbox[0].subject)

    def test_acknowledgement_id_is_stored_without_angle_brackets(self):
        process_parsed_mail(make_parsed(subject="[BUG] Bracket check"))
        ack_id = SupportTicket.objects.get().ack_message_id
        self.assertTrue(ack_id)
        self.assertNotIn("<", ack_id)
        self.assertNotIn(">", ack_id)

    def test_ack_failure_does_not_roll_back_the_ticket(self):
        from unittest.mock import patch

        with patch(
            "apps.tickets.services.ack_service.EmailMultiAlternatives.send",
            side_effect=OSError("smtp down"),
        ):
            process_parsed_mail(make_parsed(subject="[BUG] Ack failure"))
            job = OutboundMail.objects.get(kind="ACK")
            deliver_outbound_mail(job.pk)

        ticket = SupportTicket.objects.get()
        self.assertIsNone(ticket.ack_sent_at)
        self.assertEqual(ticket.ack_message_id, job.message_id)
        job.refresh_from_db()
        self.assertEqual(job.status, OutboundMail.Status.RETRY)
        self.assertEqual(
            MailIntake.objects.get().processing_status,
            MailProcessingStatus.TICKET_CREATED,
        )

    def test_no_acknowledgement_to_a_daemon_address(self):
        process_parsed_mail(
            make_parsed(subject="[BUG] x", sender="noreply@vendor.com")
        )
        self.assertEqual(len(django_mail.outbox), 0)
