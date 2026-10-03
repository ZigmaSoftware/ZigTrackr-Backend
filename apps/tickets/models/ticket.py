"""The SupportTicket model (spec 21, 22).

A lightweight intake record that sits in front of the existing Bug domain rather
than replacing it. Email always lands here first; a Bug row is created only when
a human confirms the classification and supplies the project, priority and
severity that bug_tracker requires and an email cannot supply.

That indirection is the whole point of the model. Bug.project, Bug.priority and
Bug.severity are non-nullable, and Bug.reported_by is a PROTECT foreign key to a
real user -- an email from an unknown sender satisfies none of those. Without a
ticket in front, intake would have to invent a fallback project and fill the bug
list with unreviewed external text.
"""

from django.conf import settings
from django.db import models

from apps.tickets.constants import (
    ClassificationMethod,
    ClassificationStatus,
    TicketSource,
    TicketStatus,
    TicketType,
)
from common.models import BaseMaster


class SupportTicket(BaseMaster):
    id = models.BigAutoField(primary_key=True)

    # ---- IDENTITY ----
    # The intake reference, REF-YYMM-NNNN. Allocated the moment the request
    # lands, so there is always something to quote back to the requester.
    ref_no = models.CharField(max_length=20, editable=False, default="")
    # The ticket number, TKT-YYMM-NNNN. NULL until the ticket is reviewed and
    # routed to an owner -- an unrouted request has a reference, not a number.
    #
    # NULL rather than "": MariaDB unique indexes ignore NULLs, so this keeps a
    # real database-enforced unique index while allowing many un-numbered rows.
    # A conditional UniqueConstraint would NOT work here -- MariaDB silently
    # creates nothing for one (models.W036), leaving no constraint at all.
    ticket_no = models.CharField(
        max_length=20, editable=False, null=True, blank=True, default=None,
    )
    source = models.CharField(
        max_length=20, choices=TicketSource.choices, default=TicketSource.MANUAL,
    )

    # ---- CLASSIFICATION ----
    ticket_type = models.CharField(
        max_length=20, choices=TicketType.choices, default=TicketType.UNKNOWN,
    )
    classification_method = models.CharField(
        max_length=20, choices=ClassificationMethod.choices,
        default=ClassificationMethod.MANUAL,
    )
    classification_score = models.PositiveSmallIntegerField(null=True, blank=True)
    classification_status = models.CharField(
        max_length=20, choices=ClassificationStatus.choices,
        default=ClassificationStatus.NEEDS_REVIEW,
    )
    classification_reason = models.TextField(blank=True, default="")
    needs_review = models.BooleanField(default=True)

    # ---- ROUTING (nullable: an unclassified ticket has none of these yet) ----
    project = models.ForeignKey(
        "masters.ProjectMaster", on_delete=models.PROTECT, null=True, blank=True,
        related_name="support_tickets", db_column="project_id",
    )
    module = models.ForeignKey(
        "masters.ModuleMaster", on_delete=models.PROTECT, null=True, blank=True,
        related_name="support_tickets", db_column="module_id",
    )
    submodule = models.ForeignKey(
        "masters.SubmoduleMaster", on_delete=models.PROTECT, null=True, blank=True,
        related_name="support_tickets", db_column="submodule_id",
    )
    # NOTE: spec 22 also lists category_id. There is no Category master in this
    # codebase, and a nullable integer with no foreign key target is an invitation
    # to store a module id in it. Add the column with a real FK when
    # TicketCategoryMaster exists.

    # ---- CONTENT ----
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True, default="")

    # ---- REPORTER ----
    # Non-nullable, unlike spec 22's nullable suggestion. For EMAIL tickets this
    # is always the Mail Intake system user and the true sender lives in
    # reported_by_email; a non-null FK then removes null handling from every
    # selector and serializer that touches a reporter.
    reported_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
        related_name="reported_tickets", db_column="reported_by",
    )
    reported_by_email = models.CharField(max_length=320, blank=True, default="")
    reported_by_name = models.CharField(max_length=150, blank=True, default="")

    # ---- OWNERSHIP / TRIAGE ----
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
        related_name="owned_tickets", db_column="owner_id",
    )
    priority = models.ForeignKey(
        "masters.PriorityMaster", on_delete=models.PROTECT, null=True, blank=True,
        related_name="support_tickets", db_column="priority_id",
    )
    status = models.CharField(
        max_length=25, choices=TicketStatus.choices, default=TicketStatus.NEW,
    )
    expected_closure_date = models.DateField(null=True, blank=True)

    # ---- BUG LINKAGE ----
    # Null until an Admin/Team Lead confirms. OneToOne because one ticket yields
    # at most one bug; PROTECT because bugs are never hard-deleted.
    bug = models.OneToOneField(
        "bugs.Bug", on_delete=models.PROTECT, null=True, blank=True,
        related_name="support_ticket", db_column="bug_id",
    )
    confirmed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
        related_name="confirmed_tickets", db_column="confirmed_by",
    )
    confirmed_at = models.DateTimeField(null=True, blank=True)

    # ---- ACKNOWLEDGEMENT ----
    # The Message-ID of the acknowledgement we sent, stored WITHOUT angle
    # brackets to match MailIntake.message_id. A user's reply carries this value
    # in its In-Reply-To header, so it is one half of thread matching; storing the
    # two columns with inconsistent bracketing is the classic silent failure here.
    ack_sent_at = models.DateTimeField(null=True, blank=True)
    ack_message_id = models.CharField(max_length=255, blank=True, default="")

    class Meta:
        db_table = "support_ticket"
        ordering = ["-id"]
        constraints = [
            # Unconditional, as with uq_bug_tracker_bug_no: tickets are only ever
            # soft-deleted, so the number must stay unique for all time. Rows
            # awaiting review hold NULL here, which a MariaDB unique index
            # ignores, so many may coexist.
            models.UniqueConstraint(
                fields=["ticket_no"], name="uq_support_ticket_ticket_no"
            ),
            # ref_no is NOT NULL with a "" default so it could be added to
            # existing rows; the data migration fills every one, and every code
            # path allocates one at creation. Uniqueness is therefore safe.
            models.UniqueConstraint(
                fields=["ref_no"], name="uq_support_ticket_ref_no"
            ),
        ]
        indexes = [
            models.Index(fields=["status", "is_deleted"], name="idx_ticket_status_del"),
            models.Index(fields=["ticket_type", "status"], name="idx_ticket_type_status"),
            models.Index(fields=["needs_review", "created_at"], name="idx_ticket_review"),
            models.Index(fields=["owner", "status"], name="idx_ticket_owner_status"),
            models.Index(fields=["project", "status"], name="idx_ticket_project_stat"),
            models.Index(fields=["reported_by_email"], name="idx_ticket_reporter_mail"),
            models.Index(fields=["ack_message_id"], name="idx_ticket_ack_msg_id"),
        ]

    def __str__(self):
        # record_audit() uses str(entity) for entity_label when the row has no
        # bug_no attribute, so this is what shows up in the audit log.
        return self.reference or f"Ticket #{self.pk}"

    @property
    def reference(self):
        """What to show a human: the TKT number once routed, else the ref."""
        return self.ticket_no or self.ref_no or ""

    @property
    def original_mail(self):
        mails = getattr(self, "_original_mails", None)
        if mails is not None:
            return mails[0] if mails else None
        return self.mails.filter(is_thread_reply=False).order_by("id").first()
