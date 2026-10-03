"""Linking a reply to the ticket it belongs to (spec 12).

A reply must never create a second ticket. Detection runs in spec 12's order and
stops at the first certain answer; when nothing is certain it says so, and the
message goes to review rather than being attached to a guess.

The In-Reply-To step matches against TWO columns, which is easy to get wrong:
when a user replies to our acknowledgement, their In-Reply-To points at OUR
acknowledgement's Message-ID (SupportTicket.ack_message_id), not at their own
original message (MailIntake.message_id). Checking only the latter silently
fails for every acknowledged ticket.
"""

import logging
import re
from dataclasses import dataclass

from django.db import transaction
from django.db.models import Q

from apps.mail_intake.constants import MAX_REFERENCE_TOKENS, MailProcessingStatus
from apps.mail_intake.models import MailIntake
from apps.mail_intake.services.mail_parser import parse_message_ids

logger = logging.getLogger(__name__)

# TKT-YYMM-NNNN anywhere in the subject, however the client mangled the prefix.
# Both series are matchable: the acknowledgement quotes REF- while the request
# is awaiting review, and TKT- once it has been routed, so a long thread can
# legitimately carry either in its subject.
TICKET_NO_RE = re.compile(r"\b(?:TKT|REF)-\d{4}-\d{4}\b", flags=re.IGNORECASE)

METHOD_TICKET_NO = "TICKET_NO_SUBJECT"
METHOD_IN_REPLY_TO = "IN_REPLY_TO"
METHOD_REFERENCES = "REFERENCES"
METHOD_PROVIDER_THREAD = "PROVIDER_THREAD"
METHOD_NONE = ""


@dataclass(frozen=True)
class ThreadMatch:
    ticket: object = None
    method: str = METHOD_NONE
    is_certain: bool = False
    reason: str = ""

    @property
    def matched(self):
        return self.ticket is not None


def extract_ticket_no(subject):
    """Pull TKT-YYMM-NNNN out of a subject line, upper-cased."""
    if not subject:
        return None
    match = TICKET_NO_RE.search(subject)
    return match.group(0).upper() if match else None


def _tickets_for_message_ids(message_ids):
    """Every live ticket reachable from these message ids, by either route."""
    from apps.tickets.models import SupportTicket

    ids = [m for m in message_ids if m]
    if not ids:
        return []

    # Route 1: the reply points at our acknowledgement.
    by_ack = SupportTicket.objects.filter(
        ack_message_id__in=ids, is_deleted=False
    )
    # Route 2: the reply points at a message we already stored.
    by_mail = SupportTicket.objects.filter(
        mails__message_id__in=ids, is_deleted=False
    )

    seen = {}
    for ticket in list(by_ack) + list(by_mail):
        seen[ticket.pk] = ticket
    return list(seen.values())


def find_thread_ticket(mail):
    """Identify the ticket this message continues, per spec 12's order."""
    from apps.tickets.models import SupportTicket

    # 1. An explicit ticket number in the subject. The most robust signal,
    #    because it survives a user composing a fresh message that merely
    #    quotes the old subject.
    ticket_no = extract_ticket_no(mail.subject)
    if ticket_no:
        ticket = SupportTicket.objects.filter(
            Q(ticket_no__iexact=ticket_no) | Q(ref_no__iexact=ticket_no),
            is_deleted=False,
        ).first()
        if ticket is not None:
            return ThreadMatch(ticket, METHOD_TICKET_NO, True, f"Subject cites {ticket_no}.")
        logger.info("Subject cites unknown ticket %s", ticket_no)

    # 2. In-Reply-To, against both our acknowledgement and stored mail.
    if mail.in_reply_to:
        tickets = _tickets_for_message_ids([mail.in_reply_to])
        if len(tickets) == 1:
            return ThreadMatch(
                tickets[0], METHOD_IN_REPLY_TO, True, "In-Reply-To matches a known message."
            )
        if len(tickets) > 1:
            return ThreadMatch(
                None, METHOD_IN_REPLY_TO, False,
                "In-Reply-To matches more than one ticket.",
            )

    # 3. References. Only the tail matters -- those are the immediate parents.
    references = parse_message_ids(mail.references_header, limit=MAX_REFERENCE_TOKENS)
    if references:
        tickets = _tickets_for_message_ids(references)
        if len(tickets) == 1:
            return ThreadMatch(
                tickets[0], METHOD_REFERENCES, True, "References matches a known message."
            )
        if len(tickets) > 1:
            return ThreadMatch(
                None, METHOD_REFERENCES, False,
                "References matches more than one ticket; cannot choose.",
            )

    # 4. The provider's own thread id, for messages we have seen before.
    if mail.provider_thread_id:
        ticket_ids = set(
            MailIntake.objects.filter(
                provider_thread_id=mail.provider_thread_id,
                linked_ticket__isnull=False,
            )
            .exclude(pk=mail.pk)
            .values_list("linked_ticket_id", flat=True)
        )
        if len(ticket_ids) == 1:
            ticket = SupportTicket.objects.filter(
                pk=ticket_ids.pop(), is_deleted=False
            ).first()
            if ticket is not None:
                return ThreadMatch(
                    ticket, METHOD_PROVIDER_THREAD, True, "Provider thread id matches."
                )
        elif len(ticket_ids) > 1:
            return ThreadMatch(
                None, METHOD_PROVIDER_THREAD, False,
                "Provider thread spans more than one ticket.",
            )

    # 5. Nothing certain. Spec 12 is explicit: review rather than guess.
    return ThreadMatch()


@transaction.atomic
def apply_thread_update(*, mail, ticket, actor=None, request=None):
    """Attach a reply to its ticket without creating anything new."""
    from apps.audit.models import AuditAction
    from apps.mail_intake.services.history_service import transition
    from apps.mail_intake.services.mail_normalizer import clean_readable_body
    from apps.tickets.constants import TicketUpdateSource
    from apps.tickets.services.ticket_service import add_ticket_update
    from common.services.audit import record_audit

    body = clean_readable_body(mail.body_text, mail.body_html)
    excerpt = body[:4000] or "(no body)"
    note = f"Email reply from {mail.from_email}"

    mail.linked_ticket = ticket
    mail.is_thread_reply = True
    mail.save(update_fields=["linked_ticket", "is_thread_reply", "updated_at"])

    if ticket.bug_id:
        # The ticket is confirmed, so the reply belongs on the bug's timeline.
        # add_update() is the only sanctioned way in: it refreshes the
        # denormalised latest_* columns the dashboard reads.
        from apps.bugs.services.update_service import add_update

        add_update(
            bug=ticket.bug,
            actor=actor or _system_user(),
            update_text=excerpt,
            remarks=note,
            is_system_generated=True,
            request=request,
        )
    else:
        # No bug yet. Without this branch the reply would simply be lost.
        add_ticket_update(
            ticket=ticket,
            update_text=excerpt,
            actor=actor,
            source=TicketUpdateSource.EMAIL,
            mail=mail,
            remarks=note,
        )

    transition(
        mail=mail,
        to_status=MailProcessingStatus.THREAD_UPDATE,
        action="THREAD_LINKED",
        remarks=f"Linked to {ticket.reference}.",
        actor=actor,
        processed=True,
    )

    record_audit(
        action=AuditAction.MAIL_THREAD_LINKED,
        entity=ticket,
        actor=actor or _system_user(),
        new_value=ticket.reference,
        remarks=note,
        metadata={"source": "SYSTEM", "mail_id": str(mail.unique_id)},
        request=request,
    )
    return ticket


def _system_user():
    from apps.accounts.services.system_user import get_mail_intake_user

    return get_mail_intake_user()
