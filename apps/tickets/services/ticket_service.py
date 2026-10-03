"""Support ticket creation and confirmation.

The ticket-first rule lives here: intake creates tickets, and ONLY
confirm_classification() may create a Bug, because only a human can supply the
project, priority and severity that bug_tracker requires and an email cannot.

Bug creation itself is delegated to apps.bugs.services.bug_service.create_bug --
never reimplemented. That service owns bug numbering, the SLA-derived closure
date and the opening BugStatusHistory row, and a second implementation would
drift from it.
"""

import logging
import re

from django.db import transaction
from django.utils import timezone

from apps.audit.models import AuditAction
from apps.tickets.constants import (
    WORK_TRANSITIONS,
    ClassificationMethod,
    ClassificationStatus,
    TicketSource,
    TicketStatus,
    TicketType,
    TicketUpdateSource,
)
from apps.tickets.models import SupportTicket, TicketUpdate
from apps.tickets.services.ticket_number import generate_ref_no, generate_ticket_no
from common.services.audit import record_audit
from common.utils.dates import local_today

logger = logging.getLogger(__name__)

MAX_TITLE_LENGTH = 255
NO_SUBJECT_TITLE = "(no subject)"
BUG_SUBJECT_RE = re.compile(r"^\s*\[BUG\](?:\s|$)", re.IGNORECASE)
SERVICE_SUBJECT_RE = re.compile(r"^\s*\[SERVICE\](?:\s|$)", re.IGNORECASE)
ACCESS_SUBJECT_RE = re.compile(r"^\s*\[ACCESS\](?:\s|$)", re.IGNORECASE)
SUBJECT_MARKER_RE = re.compile(r"^\s*\[(BUG|SERVICE|ACCESS)\](?:\s|$)", re.IGNORECASE)


def is_bug_subject(subject):
    return bool(BUG_SUBJECT_RE.match(subject or ""))


def explicit_subject_ticket_type(subject):
    match = SUBJECT_MARKER_RE.match(subject or "")
    if not match:
        return None
    marker = match.group(1).upper()
    return {
        "BUG": TicketType.BUG,
        "SERVICE": TicketType.SERVICE_REQUEST,
        "ACCESS": TicketType.ACCESS_REQUEST,
    }[marker]


def bug_title_from_subject(subject):
    title = SUBJECT_MARKER_RE.sub("", subject or "", count=1).strip()
    return title[:MAX_TITLE_LENGTH] or NO_SUBJECT_TITLE


def _status_for(ticket_type, needs_review):
    if needs_review or ticket_type == TicketType.UNKNOWN:
        return TicketStatus.NEEDS_REVIEW
    if ticket_type == TicketType.ACCESS_REQUEST:
        # Access never skips approval, however confident the classifier was.
        return TicketStatus.PENDING_APPROVAL
    return TicketStatus.NEW


@transaction.atomic
def create_manual_ticket(*, actor, ticket_type, title, description, requester=None,
                         source=TicketSource.MANUAL, reported_by_email="",
                         reported_by_name="", request=None):
    """Create a minimal manually entered SupportTicket.

    Assignment/workflow fields are deliberately absent from this intake step;
    Admin/TL enriches and routes the ticket from the unassigned queue.
    """
    reporter = requester or actor
    email = reported_by_email or getattr(reporter, "email", "") or ""
    name = reported_by_name or getattr(reporter, "display_name", "") or ""
    ticket = SupportTicket.objects.create(
        ref_no=generate_ref_no(local_today()),
        source=source,
        ticket_type=ticket_type,
        classification_method=ClassificationMethod.MANUAL,
        classification_status=ClassificationStatus.HUMAN_CONFIRMED,
        classification_reason="Manual ticket intake.",
        needs_review=False,
        title=(title or "").strip()[:MAX_TITLE_LENGTH] or NO_SUBJECT_TITLE,
        description=description or "",
        reported_by=reporter,
        reported_by_email=email,
        reported_by_name=name,
        status=TicketStatus.NEW,
        created_by=getattr(actor, "unique_id", None),
        updated_by=getattr(actor, "unique_id", None),
    )
    record_audit(
        action=AuditAction.TICKET_CREATED,
        entity=ticket,
        actor=actor,
        new_value=ticket.reference,
        remarks="Manual ticket created.",
        metadata={"source": str(source), "ticket_type": ticket_type},
        request=request,
    )
    from apps.tickets.services.activity_service import record_activity

    record_activity(
        ticket=ticket, event_type="TICKET_RECEIVED", title="Ticket received",
        description=f"The request was received from {name or email} and registered as {ticket.ref_no}.",
        public_description=f"Your request was received and registered as {ticket.ref_no}.",
        actor=actor,
    )
    return ticket


@transaction.atomic
def create_ticket_from_mail(*, mail, classification, actor=None, request=None):
    """Create the support ticket for a validated message.

    Idempotent: a message that already has a linked ticket returns it rather
    than creating a second, so a reprocess or a concurrent run cannot duplicate.
    """
    if mail.linked_ticket_id:
        return mail.linked_ticket

    from apps.accounts.services.system_user import get_mail_intake_user
    from apps.mail_intake.services.mail_normalizer import (
        clean_readable_body,
        has_meaningful_content,
    )
    from apps.masters.models import ModuleMaster, ProjectMaster, SubmoduleMaster

    system_user = get_mail_intake_user()

    def resolve(model, unique_id):
        if not unique_id:
            return None
        return model.objects.filter(unique_id=unique_id, is_deleted=False).first()

    explicit_type = explicit_subject_ticket_type(mail.subject)
    direct_bug = explicit_type == TicketType.BUG and not classification.needs_review
    title = bug_title_from_subject(mail.subject) if explicit_type else (
        (mail.subject or "").strip()[:MAX_TITLE_LENGTH] or NO_SUBJECT_TITLE
    )
    description = clean_readable_body(mail.body_text, mail.body_html)
    if not has_meaningful_content(mail.subject, description):
        from common.exceptions.domain import WorkflowValidationError

        raise WorkflowValidationError(
            {"detail": ["Mail has no meaningful subject or body."]}
        )

    needs_review = bool(classification.needs_review) and not direct_bug
    ticket_type = explicit_type or (classification.ticket_type or TicketType.UNKNOWN)

    ticket = SupportTicket(
        ref_no=generate_ref_no(local_today()),
        source=TicketSource.EMAIL,
        ticket_type=ticket_type,
        classification_method=classification.classification_method
        or ClassificationMethod.RULE_BASED,
        classification_score=classification.classification_score,
        classification_status=(
            ClassificationStatus.NEEDS_REVIEW
            if needs_review
            else ClassificationStatus.AUTO_CLASSIFIED
        ),
        classification_reason=(classification.review_reason or "")[:2000],
        needs_review=needs_review,
        project=resolve(ProjectMaster, classification.project_unique_id),
        module=resolve(ModuleMaster, classification.module_unique_id),
        submodule=resolve(SubmoduleMaster, classification.submodule_unique_id),
        title=title,
        description=description,
        # Always the system user; the real sender is kept alongside. Bug and
        # ticket reporter columns are PROTECT FKs to real accounts, and an
        # external sender has none.
        reported_by=system_user,
        reported_by_email=mail.from_email,
        reported_by_name=mail.from_name,
        status=_status_for(ticket_type, needs_review),
        created_by=getattr(system_user, "unique_id", None),
        updated_by=getattr(system_user, "unique_id", None),
    )
    ticket.save()

    if direct_bug:
        # Email intake intentionally creates an operationally visible Bug with
        # only the information an email can supply. Assignment later fills in
        # the project, priority, severity, owner, and routing fields.
        from apps.bugs.services.bug_service import create_bug

        bug = create_bug(
            actor=system_user,
            request=request,
            project=None,
            module=resolve(ModuleMaster, classification.module_unique_id),
            submodule=resolve(SubmoduleMaster, classification.submodule_unique_id),
            title=title,
            description=description,
            reported_by=system_user,
            priority=None,
            severity=None,
            environment=None,
            reported_date=mail.received_at.date() if mail.received_at else None,
        )
        ticket.bug = bug
        ticket.save(update_fields=["bug", "updated_at"])

    mail.linked_ticket = ticket
    mail.save(update_fields=["linked_ticket", "updated_at"])

    record_audit(
        action=AuditAction.TICKET_CREATED_FROM_EMAIL,
        entity=ticket,
        actor=system_user,
        new_value=ticket.reference,
        remarks=f"Created from email from {mail.from_email}.",
        metadata={"source": "SYSTEM", "mail_id": str(mail.unique_id)},
        request=request,
    )
    from apps.tickets.services.activity_service import record_activity

    record_activity(
        ticket=ticket, event_type="TICKET_RECEIVED", title="Ticket received",
        description=f"The request was received from {mail.from_email} and registered as {ticket.ref_no}.",
        public_description=f"Your request was received and registered as {ticket.ref_no}.",
        actor_email=mail.from_email,
    )
    return ticket


def _require_ticket_type(ticket, allowed):
    from common.exceptions.domain import WorkflowValidationError

    if ticket.ticket_type not in allowed:
        labels = ", ".join(allowed)
        raise WorkflowValidationError({"detail": [f"This action is only valid for {labels}."]})


def _require_status(ticket, allowed, message):
    from common.exceptions.domain import WorkflowValidationError

    if ticket.status not in allowed:
        raise WorkflowValidationError({"detail": [f"{message} Current status is {ticket.status}."]})


@transaction.atomic
def transition_work(*, ticket, actor, to_status, remarks="", payload=None, request=None):
    """Move a ticket along the shared work flow.

    One function for every ticket type: Start puts it In Progress, from there
    the developer parks it (On Hold) or hands it to a tester (Testing), and the
    tester alone closes it. WORK_TRANSITIONS is the whole rule -- a move that is
    not listed there is refused rather than silently applied, so the UI and the
    API cannot disagree about what is legal.

    A BUG ticket's status lives on its linked bug, so both are moved together;
    otherwise the ticket would claim Testing while the bug still said Assigned.
    """
    from common.exceptions.domain import WorkflowValidationError
    from apps.tickets.services.activity_service import record_activity

    payload = payload or {}
    remarks = (remarks or "").strip()
    if to_status in (TicketStatus.PENDING, TicketStatus.ON_HOLD, TicketStatus.TESTING, TicketStatus.CLOSED) and not remarks:
        raise WorkflowValidationError({"remarks": ["A description is required for this action."]})

    current = ticket.bug.status if ticket.bug_id else ticket.status

    allowed = WORK_TRANSITIONS.get(current, ())
    if to_status not in allowed:
        raise WorkflowValidationError({"detail": [
            f"A {current} ticket cannot move to "
            f"{TicketStatus(to_status).label}."
        ]})
    if ticket.owner_id is None:
        raise WorkflowValidationError({"detail": ["Assign an owner before starting work."]})

    if to_status == TicketStatus.IN_PROGRESS:
        from apps.tickets.services.policy import ensure_owner_can_start

        ensure_owner_can_start(ticket.owner_id, exclude_ticket_id=ticket.pk)

    previous = current

    if ticket.bug_id:
        from apps.bugs.services.status_service import change_status

        bug = ticket.bug
        transition_payload = {}
        if to_status == TicketStatus.CLOSED:
            from apps.bugs.constants import VerificationResult
            from apps.bugs.services.closure_service import close_bug, resolve_bug

            bug = resolve_bug(
                bug=bug, actor=actor, root_cause=bug.root_cause,
                resolution=bug.resolution, remarks=remarks, request=request,
                allow_reopened_verification=current == TicketStatus.REOPENED,
            )
            close_bug(
                bug=bug, actor=actor, closure_remarks=remarks,
                verification_result=VerificationResult.PASSED,
                remarks=remarks, request=request,
            )
        elif to_status == TicketStatus.ON_HOLD:
            transition_payload["hold_reason"] = remarks
        elif to_status == TicketStatus.PENDING:
            transition_payload["pending_reason"] = remarks
        elif to_status == TicketStatus.TESTING:
            root_cause = (payload.get("root_cause") or bug.root_cause or "").strip()
            resolution = (payload.get("resolution") or bug.resolution or "").strip()
            if not root_cause or not resolution:
                raise WorkflowValidationError({
                    key: ["Required before rectification."]
                    for key, value in (("root_cause", root_cause), ("resolution", resolution))
                    if not value
                })
            bug.root_cause = root_cause
            bug.resolution = resolution
            bug.latest_remarks = remarks
            bug.save(update_fields=["root_cause", "resolution", "latest_remarks", "updated_at"])
        if to_status != TicketStatus.CLOSED:
            change_status(
                bug=bug, to_status=to_status, actor=actor, remarks=remarks,
                payload=transition_payload, request=request,
            )

    ticket.status = to_status
    ticket.updated_by = getattr(actor, "unique_id", None)
    ticket.save(update_fields=["status", "updated_by", "updated_at"])

    labels = {
        TicketStatus.IN_PROGRESS: "Work started.",
        TicketStatus.PENDING: "Moved to pending.",
        TicketStatus.ON_HOLD: "Put on hold.",
        TicketStatus.TESTING: "Rectified and sent for verification.",
        TicketStatus.CLOSED: "Verified and closed.",
    }
    add_ticket_update(
        ticket=ticket,
        update_text=labels.get(to_status, f"Moved to {TicketStatus(to_status).label}."),
        remarks=remarks,
        actor=actor,
        source=TicketUpdateSource.SYSTEM,
        request=request,
    )
    record_audit(
        action=AuditAction.STATUS_CHANGE,
        entity=ticket,
        actor=actor,
        field_name="status",
        old_value=previous,
        new_value=ticket.status,
        remarks=remarks,
        request=request,
    )
    activity = {
        TicketStatus.IN_PROGRESS: ("RETURNED_TO_DEVELOPER" if previous == TicketStatus.TESTING else "WORK_STARTED", "Work started"),
        TicketStatus.PENDING: ("TICKET_PENDING", "Ticket moved to Pending"),
        TicketStatus.ON_HOLD: ("TICKET_ON_HOLD", "Ticket placed on Hold"),
        TicketStatus.TESTING: ("TICKET_RECTIFIED", "Issue Rectified"),
        TicketStatus.CLOSED: ("TICKET_CLOSED", "Ticket Closed"),
    }
    event_type, title = activity[to_status]
    if not ticket.bug_id:
        record_activity(
            ticket=ticket, event_type=event_type, title=title,
            description=f"{actor.display_name} changed the ticket from {previous} to {to_status}. {remarks}".strip(),
            public_description=(f"Your request is now {TicketStatus(to_status).label.lower()}."),
            actor=actor,
        )
    return ticket


def start_service_work(*, ticket, actor, remarks="", request=None):
    """Move a service request into active work.

    Service tickets are not Bugs, so they need their own small workflow instead
    of borrowing bug root-cause/testing states.
    """
    _require_ticket_type(ticket, (TicketType.SERVICE_REQUEST,))
    if ticket.owner_id is None:
        from common.exceptions.domain import WorkflowValidationError

        raise WorkflowValidationError({"detail": ["Assign an owner before starting service work."]})
    _require_status(
        ticket,
        (TicketStatus.NEW, TicketStatus.CONFIRMED, TicketStatus.ASSIGNED),
        "Only a new or assigned service request can be started.",
    )

    previous = ticket.status
    ticket.status = TicketStatus.IN_PROGRESS
    ticket.updated_by = getattr(actor, "unique_id", None)
    ticket.save(update_fields=["status", "updated_by", "updated_at"])

    add_ticket_update(
        ticket=ticket,
        update_text="Service work started.",
        remarks=remarks,
        actor=actor,
        source=TicketUpdateSource.SYSTEM,
        request=request,
    )
    record_audit(
        action=AuditAction.STATUS_CHANGE,
        entity=ticket,
        actor=actor,
        field_name="status",
        old_value=previous,
        new_value=ticket.status,
        remarks=remarks,
        request=request,
    )
    from apps.tickets.services.activity_service import record_activity

    record_activity(
        ticket=ticket, event_type="WORK_STARTED", title="Service work started",
        description=f"{actor.display_name} started service work. {remarks}".strip(),
        public_description="Work on your request has started.", actor=actor,
    )
    return ticket


@transaction.atomic
def complete_service_work(*, ticket, actor, remarks="", request=None):
    _require_ticket_type(ticket, (TicketType.SERVICE_REQUEST,))
    if ticket.owner_id is None:
        from common.exceptions.domain import WorkflowValidationError

        raise WorkflowValidationError({"detail": ["Assign an owner before completing service work."]})
    _require_status(
        ticket,
        (TicketStatus.IN_PROGRESS, TicketStatus.ASSIGNED),
        "Only an active service request can be completed.",
    )

    previous = ticket.status
    ticket.status = TicketStatus.COMPLETED
    ticket.updated_by = getattr(actor, "unique_id", None)
    ticket.save(update_fields=["status", "updated_by", "updated_at"])

    add_ticket_update(
        ticket=ticket,
        update_text="Service work completed.",
        remarks=remarks,
        actor=actor,
        source=TicketUpdateSource.SYSTEM,
        request=request,
    )
    record_audit(
        action=AuditAction.STATUS_CHANGE,
        entity=ticket,
        actor=actor,
        field_name="status",
        old_value=previous,
        new_value=ticket.status,
        remarks=remarks,
        request=request,
    )
    from apps.tickets.services.activity_service import record_activity

    record_activity(
        ticket=ticket, event_type="SERVICE_COMPLETED", title="Service work completed",
        description=f"{actor.display_name} completed service work. {remarks}".strip(),
        public_description="Work on your request has been completed.", actor=actor,
    )
    return ticket


@transaction.atomic
def close_ticket(*, ticket, actor, remarks="", request=None):
    _require_ticket_type(ticket, (TicketType.SERVICE_REQUEST, TicketType.ACCESS_REQUEST))
    _require_status(
        ticket,
        (TicketStatus.COMPLETED,),
        "Only a completed service or access request can be closed.",
    )

    previous = ticket.status
    ticket.status = TicketStatus.CLOSED
    ticket.updated_by = getattr(actor, "unique_id", None)
    ticket.save(update_fields=["status", "updated_by", "updated_at"])

    add_ticket_update(
        ticket=ticket,
        update_text="Ticket closed.",
        remarks=remarks,
        actor=actor,
        source=TicketUpdateSource.SYSTEM,
        request=request,
    )
    record_audit(
        action=AuditAction.STATUS_CHANGE,
        entity=ticket,
        actor=actor,
        field_name="status",
        old_value=previous,
        new_value=ticket.status,
        remarks=remarks,
        request=request,
    )
    from apps.tickets.services.activity_service import record_activity

    record_activity(
        ticket=ticket, event_type="TICKET_CLOSED", title="Ticket Closed",
        description=f"{actor.display_name} closed the ticket. {remarks}".strip(),
        public_description="Your request has been closed.", actor=actor,
    )
    return ticket


@transaction.atomic
def add_ticket_update(*, ticket, update_text, actor=None, source=TicketUpdateSource.USER,
                      mail=None, remarks="", request=None):
    """Append an update to a ticket.

    Used for email replies that arrive before a ticket is confirmed, when there
    is no Bug yet to attach a BugUpdate to. Once a bug exists, replies go through
    apps.bugs.services.update_service.add_update() instead.
    """
    update = TicketUpdate.objects.create(
        ticket=ticket,
        update_text=update_text,
        remarks=remarks,
        source=source,
        mail=mail,
        created_by_user=actor if getattr(actor, "pk", None) else None,
    )

    if source != TicketUpdateSource.SYSTEM:
        record_audit(
            action=AuditAction.TICKET_UPDATE_ADDED,
            entity=ticket,
            actor=actor,
            new_value=update_text[:200],
            metadata={"source": "SYSTEM" if source == TicketUpdateSource.EMAIL else "USER"},
            request=request,
        )
    return update


@transaction.atomic
def confirm_classification(*, ticket, actor, ticket_type, title=None, description=None,
                           project=None, module=None, submodule=None,
                           priority=None, severity=None, owner=None,
                           environment=None, expected_closure_date=None,
                           remarks="", request=None):
    """Confirm a ticket's classification, creating the Bug when the type is BUG.

    This is the ONLY place in the codebase that turns an email into a Bug, and
    it requires a real `actor`: BugStatusHistory.changed_by is a non-nullable
    PROTECT foreign key, so a system-only confirmation is impossible by design.
    The reporter recorded on the bug stays the system user, keeping the two
    concerns -- who acted, and who reported -- honestly separate.
    """
    from apps.accounts.services.system_user import get_mail_intake_user
    from apps.bugs.services.bug_service import create_bug

    if ticket.bug_id:
        # Already confirmed. Returning the existing ticket keeps a double
        # submit idempotent rather than producing a second bug.
        return ticket

    previous_type = ticket.ticket_type
    system_user = get_mail_intake_user()

    ticket.ticket_type = ticket_type
    if title:
        ticket.title = title[:MAX_TITLE_LENGTH]
    if description is not None:
        ticket.description = description
    ticket.project = project or ticket.project
    ticket.module = module or ticket.module
    ticket.submodule = submodule or ticket.submodule
    ticket.priority = priority or ticket.priority
    ticket.owner = owner or ticket.owner
    if expected_closure_date:
        ticket.expected_closure_date = expected_closure_date

    ticket.classification_status = ClassificationStatus.HUMAN_CONFIRMED
    ticket.needs_review = False
    ticket.confirmed_by = actor
    ticket.confirmed_at = timezone.now()
    ticket.updated_by = getattr(actor, "unique_id", None)

    bug = None
    if ticket_type == TicketType.BUG:
        if not (project and priority and severity):
            from common.exceptions.domain import WorkflowValidationError

            raise WorkflowValidationError(
                "Project, priority and severity are required to create a bug."
            )

        bug_fields = {
            "project": project,
            "module": module,
            "submodule": submodule,
            "priority": priority,
            "severity": severity,
            "title": ticket.title,
            "description": ticket.description or ticket.title,
            # The confirming admin is the actor; the reporter remains the
            # system user, with the true sender on the ticket.
            "reported_by": system_user,
        }
        if environment:
            bug_fields["environment"] = environment
        if expected_closure_date:
            bug_fields["expected_closure_date"] = expected_closure_date

        bug = create_bug(actor=actor, request=request, **bug_fields)
        ticket.bug = bug
        ticket.status = TicketStatus.CONFIRMED
    elif ticket_type == TicketType.ACCESS_REQUEST:
        ticket.status = TicketStatus.PENDING_APPROVAL
    else:
        ticket.status = TicketStatus.CONFIRMED

    ticket.save()

    if previous_type != ticket_type:
        record_audit(
            action=AuditAction.TICKET_TYPE_CHANGED,
            entity=ticket, actor=actor,
            field_name="ticket_type", old_value=previous_type, new_value=ticket_type,
            request=request,
        )

    record_audit(
        action=AuditAction.MAIL_CLASSIFICATION_CONFIRMED,
        entity=ticket,
        actor=actor,
        new_value=ticket_type,
        remarks=remarks,
        metadata={"source": "USER", "bug_no": getattr(bug, "bug_no", "")},
        request=request,
    )
    return ticket


@transaction.atomic
def review_ticket(*, ticket, actor, ticket_type, title=None, description=None,
                  project=None, module=None, submodule=None, priority=None,
                  severity=None, owner=None, environment=None,
                  expected_closure_date=None, remarks="", request=None):
    """Review/enrich an unassigned ticket and optionally assign it.

    For BUG tickets this creates/links the existing Bug domain row and then uses
    assign_bug() when an owner is provided. Service/access tickets stay in the
    SupportTicket workflow.
    """
    ticket = confirm_classification(
        ticket=ticket,
        actor=actor,
        ticket_type=ticket_type,
        title=title,
        description=description,
        project=project,
        module=module,
        submodule=submodule,
        priority=priority,
        severity=severity,
        owner=None if ticket_type == TicketType.BUG else owner,
        environment=environment,
        expected_closure_date=expected_closure_date,
        remarks=remarks,
        request=request,
    )

    # Assignment is what turns a request into a ticket, so this is where the
    # TKT number is minted. Once set it never changes: a reassignment later must
    # not renumber a ticket the requester already has in their inbox.
    if owner and not ticket.ticket_no:
        ticket.ticket_no = generate_ticket_no(local_today())
        ticket.updated_by = getattr(actor, "unique_id", None)
        ticket.save(update_fields=["ticket_no", "updated_by", "updated_at"])
        record_audit(
            action=AuditAction.TICKET_CREATED,
            entity=ticket,
            actor=actor,
            old_value=ticket.ref_no,
            new_value=ticket.ticket_no,
            remarks=f"Ticket number issued on assignment for {ticket.ref_no}.",
            metadata={"source": "REVIEW", "ticket_type": ticket_type},
            request=request,
        )

    if ticket_type == TicketType.BUG and owner and ticket.bug_id:
        from apps.bugs.services.assignment_service import assign_bug

        bug = assign_bug(
            bug=ticket.bug,
            new_owner=owner,
            actor=actor,
            remarks=remarks,
            expected_closure_date=expected_closure_date,
            request=request,
        )
        ticket.owner = owner
        ticket.status = TicketStatus.ASSIGNED
        ticket.expected_closure_date = bug.expected_closure_date
        ticket.updated_by = getattr(actor, "unique_id", None)
        ticket.save(update_fields=[
            "owner", "status", "expected_closure_date", "updated_by", "updated_at",
        ])
    elif ticket_type == TicketType.SERVICE_REQUEST and owner:
        ticket.owner = owner
        ticket.status = TicketStatus.ASSIGNED
        if expected_closure_date:
            ticket.expected_closure_date = expected_closure_date
        ticket.updated_by = getattr(actor, "unique_id", None)
        ticket.save(update_fields=[
            "owner", "status", "expected_closure_date", "updated_by", "updated_at",
        ])
    elif ticket_type == TicketType.ACCESS_REQUEST and owner:
        # Stores the intended implementer, but status remains pending approval.
        ticket.owner = owner
        if expected_closure_date:
            ticket.expected_closure_date = expected_closure_date
        ticket.updated_by = getattr(actor, "unique_id", None)
        ticket.save(update_fields=[
            "owner", "expected_closure_date", "updated_by", "updated_at",
        ])

    if owner:
        from apps.tickets.models import TicketAssignmentHistory
        from apps.tickets.services.activity_service import record_activity

        TicketAssignmentHistory.objects.create(
            ticket=ticket, to_owner=owner, performed_by=actor, reason=remarks,
        )
        if not ticket.bug_id:
            record_activity(
                ticket=ticket, event_type="TICKET_ASSIGNED", title="Ticket Assigned",
                description=f"The ticket was assigned to {owner.display_name} by {actor.display_name}.",
                public_description="Your request has been assigned to a support specialist.",
                actor=actor,
            )

    return ticket
