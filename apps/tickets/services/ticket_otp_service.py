"""Emailed one-time passcodes guarding the public tracking page.

Sign-in is by email address alone: a code delivered to that mailbox makes
possession of the mailbox the proof of identity, and one verification then
covers every ticket the address owns. An address on its own is often public,
so the code is the whole of the security here.

Two rules shape this module:

  1. `issue_otp` never tells the caller whether the address owns anything.
     The view answers identically either way, so the page cannot be used to
     discover which addresses have raised tickets.
  2. The attempt counter lives in the database, not the client, and is
     incremented under a row lock. Parallel guesses cannot each read the same
     pre-increment value and collectively exceed the limit.
"""

import hashlib
import hmac
import logging
import secrets
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.audit.models import AuditAction
from apps.tickets.models import TicketAccessOtp
from common.exceptions.domain import WorkflowValidationError
from common.services.audit import record_audit

logger = logging.getLogger(__name__)

OTP_TTL_MINUTES = 10
OTP_TTL = timedelta(minutes=OTP_TTL_MINUTES)
MAX_ATTEMPTS = 5
CODE_LENGTH = 6


def _hash_code(code):
    """Keyed hash: a leaked row must not be convertible back to a code."""
    return hashlib.sha256(
        f"{code}{settings.SECRET_KEY}".encode("utf-8")
    ).hexdigest()


def _client_ip(request):
    if request is None:
        return None
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR") or None


def generate_code():
    # randbelow is the CSPRNG; zero-padded so every code is the same length.
    return f"{secrets.randbelow(10 ** CODE_LENGTH):0{CODE_LENGTH}d}"


def issue_otp(*, email, request=None):
    """Send a passcode when the address owns at least one tracked ticket.

    Keyed on the address alone: signing in gives access to every ticket that
    address owns, so requiring a ticket number here would be asking for a
    detail the requester may not have to hand.

    Returns the anchor ticket, or None when the address owns nothing. The
    None case must be indistinguishable to the view, which answers the same
    way either way.
    """
    from apps.tickets.services.public_track_service import tickets_for_email

    normalised = email.strip().lower()
    # The OTP row needs a ticket FK; the most recent one anchors it. Access is
    # later granted by address, not by this ticket, so the choice only affects
    # which ticket the audit entry is filed against.
    anchor = tickets_for_email(normalised).first()
    if anchor is None:
        return None

    code = generate_code()
    now = timezone.now()

    with transaction.atomic():
        # Supersede anything outstanding, so a resend invalidates the code in
        # the previous email rather than leaving two valid at once.
        TicketAccessOtp.objects.filter(
            email=normalised, consumed_at__isnull=True,
        ).update(consumed_at=now)

        otp = TicketAccessOtp.objects.create(
            ticket=anchor,
            email=normalised,
            code_hash=_hash_code(code),
            expires_at=now + OTP_TTL,
            request_ip=_client_ip(request),
        )

    # Queued after the row is committed: a code that was emailed but not
    # recorded would be unverifiable.
    from apps.tickets.services.outbound_mail import queue_access_otp

    queue_access_otp(ticket=anchor, to_email=normalised, code=code, otp=otp)

    record_audit(
        action=AuditAction.TICKET_OTP_SENT,
        entity=anchor,
        remarks="Access code emailed to the requester.",
        request=request,
    )
    return anchor


def verify_otp(*, email, code, request=None):
    """Return the verified address when the code is right; raise otherwise."""
    from apps.tickets.services.public_track_service import tickets_for_email

    normalised = email.strip().lower()
    generic = WorkflowValidationError(
        {"code": ["That code is not valid. Request a new one and try again."]}
    )
    anchor = tickets_for_email(normalised).first()
    # Reported as a bad code rather than "no such address": the error must not
    # become the oracle the request step is designed to avoid.
    if anchor is None:
        raise generic

    now = timezone.now()

    # The failure path must COMMIT the incremented counter, so the error is
    # raised after the transaction closes rather than inside it. Raising within
    # the atomic block rolls the increment back, which silently defeats the
    # attempt limit entirely -- an attacker could then guess without bound.
    failure = None
    with transaction.atomic():
        otp = (
            TicketAccessOtp.objects.select_for_update()
            .filter(email=normalised, consumed_at__isnull=True)
            .order_by("-created_at")
            .first()
        )
        if otp is None or otp.expires_at <= now or otp.attempts >= MAX_ATTEMPTS:
            failure = generic
        elif not hmac.compare_digest(otp.code_hash, _hash_code(code.strip())):
            otp.attempts += 1
            otp.save(update_fields=["attempts"])
            remaining = max(MAX_ATTEMPTS - otp.attempts, 0)
            failure = WorkflowValidationError({"code": [
                "Too many incorrect attempts. Request a new code."
                if remaining == 0 else
                f"Incorrect code. {remaining} attempt"
                f"{'s' if remaining != 1 else ''} remaining."
            ]})
        else:
            otp.consumed_at = now
            otp.save(update_fields=["consumed_at"])

    if failure is not None:
        record_audit(
            action=AuditAction.TICKET_OTP_FAILED,
            entity=anchor,
            remarks="Incorrect or expired access code submitted.",
            request=request,
        )
        raise failure

    record_audit(
        action=AuditAction.TICKET_OTP_VERIFIED,
        entity=anchor,
        remarks="Requester verified their access code.",
        request=request,
    )
    return normalised
