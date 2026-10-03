"""Test helpers for tickets."""

from apps.bugs.tests.factories import make_masters, make_user
from apps.tickets.constants import ClassificationMethod, TicketSource, TicketStatus, TicketType
from apps.tickets.models import SupportTicket
from apps.tickets.services.ticket_number import generate_ref_no
from common.utils.dates import local_today

__all__ = ["make_masters", "make_ticket", "make_user"]


def make_ticket(*, reporter=None, ticket_type=TicketType.UNKNOWN, needs_review=True,
                source=TicketSource.EMAIL, title="Test ticket", **overrides):
    if reporter is None:
        from apps.accounts.services.system_user import get_mail_intake_user

        reporter = get_mail_intake_user()

    fields = {
        # Matches intake: a reference now, a TKT number only once routed. Pass
        # ticket_no=... explicitly to model an already-assigned ticket.
        "ref_no": generate_ref_no(local_today()),
        "source": source,
        "ticket_type": ticket_type,
        "classification_method": ClassificationMethod.RULE_BASED,
        "needs_review": needs_review,
        "title": title,
        "description": "Description text.",
        "reported_by": reporter,
        "reported_by_email": "sender@example.com",
        "status": TicketStatus.NEEDS_REVIEW if needs_review else TicketStatus.NEW,
    }
    fields.update(overrides)
    return SupportTicket.objects.create(**fields)
