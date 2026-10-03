"""Mail intake API (spec 34).

ReadOnlyModelViewSet plus explicit actions: mail is received, never created or
edited through the API. Every state change goes through a named action whose
permission is declared below.

IMPORTANT: PermissionByActionMixin is fail-open -- an action missing from
permission_map falls through to bare IsAuthenticated. Every action here has an
entry, including read-only ones, and test_permissions.py asserts by
introspection that this stays true.
"""

import logging

from django.db import transaction
from django.utils import timezone
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import IsAuthenticated

from apps.audit.models import AuditAction
from apps.mail_intake.constants import MailProcessingStatus
from apps.mail_intake.filters import MailIntakeFilter
from apps.mail_intake.models import MailIntake
from apps.mail_intake.serializers import (
    ConfirmClassificationSerializer,
    IgnoreSerializer,
    MailAttachmentSerializer,
    MailIntakeDetailSerializer,
    MailIntakeListSerializer,
    MailProcessingHistorySerializer,
    ReprocessSerializer,
)
from common.db.locks import advisory_lock
from common.permissions.require import RequirePermission
from common.permissions.scoping import scope_mail_queryset
from common.responses import EnvelopeMessageMixin, ok
from common.services.audit import record_audit
from common.viewsets import PermissionByActionMixin

logger = logging.getLogger(__name__)

RUN_LOCK = "mail_intake_run"


class MailIntakeViewSet(PermissionByActionMixin, EnvelopeMessageMixin,
                        viewsets.ReadOnlyModelViewSet):
    lookup_field = "unique_id"
    lookup_url_kwarg = "unique_id"
    permission_classes = [IsAuthenticated]
    filterset_class = MailIntakeFilter
    search_fields = []
    ordering_fields = ["received_at", "processing_status", "from_email", "subject"]
    ordering = ["-received_at", "-id"]

    permission_map = {
        "list": [RequirePermission("mail_intake.mail.view")],
        "retrieve": [RequirePermission("mail_intake.mail.view")],
        "needs_review": [RequirePermission("mail_intake.mail.view")],
        "stats": [RequirePermission("mail_intake.mail.view")],
        "history": [RequirePermission("mail_intake.mail.view")],
        "attachments": [RequirePermission("mail_intake.mail.view")],
        "body": [RequirePermission("mail_intake.mail.view")],
        # Separate codename: rule patterns and scores are business
        # configuration, so seeing them is a different question from seeing
        # the mail (spec 39).
        "diagnostics": [RequirePermission("mail_intake.mail.view_diagnostics")],
        "reprocess": [RequirePermission("mail_intake.mail.reprocess")],
        "confirm_classification": [
            RequirePermission("mail_intake.mail.confirm_classification")
        ],
        "ignore": [RequirePermission("mail_intake.mail.ignore")],
        "process_now": [RequirePermission("mail_intake.mail.process_now")],
    }

    envelope_messages = {
        "reprocess": "Mail reprocessed.",
        "confirm_classification": "Classification confirmed.",
        "ignore": "Mail ignored.",
        "process_now": "Mail fetch complete.",
    }

    def get_queryset(self):
        queryset = (
            MailIntake.objects.select_related(
                "linked_ticket", "linked_ticket__project",
                "linked_ticket__module", "linked_ticket__submodule",
            )
            .prefetch_related("attachments")
        )
        return scope_mail_queryset(queryset, self.request.user)

    def get_serializer_class(self):
        if self.action == "list":
            return MailIntakeListSerializer
        return MailIntakeDetailSerializer

    # ---- READ ACTIONS ----

    @action(detail=False, methods=["get"], url_path="needs-review")
    def needs_review(self, request):
        """The review queue. A filtered list, not a second implementation."""
        queryset = self.filter_queryset(self.get_queryset()).filter(
            linked_ticket__needs_review=True
        )
        page = self.paginate_queryset(queryset)
        serializer = MailIntakeListSerializer(page or queryset, many=True)
        if page is not None:
            return self.get_paginated_response(serializer.data)
        return ok(serializer.data)

    @action(detail=True, methods=["get"])
    def history(self, request, unique_id=None):
        mail = self.get_object()
        rows = mail.processing_history.select_related("performed_by").all()
        return ok(MailProcessingHistorySerializer(rows, many=True).data)

    @action(detail=True, methods=["get"])
    def attachments(self, request, unique_id=None):
        mail = self.get_object()
        return ok(MailAttachmentSerializer(mail.attachments.all(), many=True).data)

    @action(detail=True, methods=["get"])
    def body(self, request, unique_id=None):
        """The sanitised HTML body, for rendering in a sandboxed iframe.

        Only ever the sanitised copy: raw body_html is attacker-controlled and
        is never serialised anywhere.
        """
        mail = self.get_object()
        response = ok(
            {
                "html": mail.body_html_sanitized or "",
                "text": mail.body_text or "",
                "has_html": bool(mail.body_html_sanitized),
            }
        )
        # Backstop in case the client-side iframe sandbox is ever weakened.
        response["Content-Security-Policy"] = (
            "default-src 'none'; img-src https: data: cid:; style-src 'unsafe-inline'"
        )
        response["X-Content-Type-Options"] = "nosniff"
        return response

    @action(detail=True, methods=["get"])
    def diagnostics(self, request, unique_id=None):
        """Why the classifier decided what it did."""
        mail = self.get_object()
        audit = mail.classification_audits.order_by("-id").first()
        if audit is None:
            return ok({})
        return ok(
            {
                "predicted_type": audit.rule_predicted_type,
                "score": audit.rule_score,
                "all_scores": audit.all_scores,
                "matched_rules": audit.matched_rules,
                "review_reason": audit.review_reason,
                "rule_project": getattr(audit.rule_project, "name", ""),
                "rule_module": getattr(audit.rule_module, "name", ""),
                "human_final_type": audit.human_final_type,
                "corrected_at": audit.corrected_at,
            }
        )

    @action(detail=False, methods=["get"])
    def stats(self, request):
        """Operational counters for the dashboard (spec 48)."""
        from django.db.models import Count

        queryset = self.get_queryset()
        today = timezone.localdate()

        # order_by() cleared first: Meta.ordering would otherwise leak into the
        # GROUP BY and make every count 1.
        by_status = dict(
            queryset.order_by()
            .values_list("processing_status")
            .annotate(total=Count("id"))
        )
        return ok(
            {
                "received_today": queryset.filter(received_at__date=today).count(),
                "tickets_created": queryset.filter(linked_ticket__isnull=False).count(),
                "needs_review": queryset.filter(linked_ticket__needs_review=True).count(),
                "duplicates": by_status.get(MailProcessingStatus.DUPLICATE, 0),
                "thread_updates": by_status.get(MailProcessingStatus.THREAD_UPDATE, 0),
                "rejected": by_status.get(MailProcessingStatus.REJECTED, 0),
                "processing_failed": by_status.get(
                    MailProcessingStatus.PROCESSING_FAILED, 0
                ),
                "by_status": by_status,
            }
        )

    # ---- WRITE ACTIONS ----

    @action(detail=True, methods=["post"])
    def reprocess(self, request, unique_id=None):
        """Re-drive one message through the pipeline.

        Refuses once a ticket exists: reprocessing must never produce a second.
        """
        from apps.mail_intake.services.intake_orchestrator import (
            IntakeRunResult,
            _continue_processing,
        )

        mail = self.get_object()
        serializer = ReprocessSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        if mail.linked_ticket_id and mail.processing_status == MailProcessingStatus.TICKET_CREATED:
            raise ValidationError(
                {"detail": [f"This mail already produced ticket {mail.linked_ticket.reference}."]}
            )

        # A human is asserting intent, so the attempt cap does not apply.
        mail.last_error_code = ""
        mail.last_error_message = ""
        mail.save(update_fields=["last_error_code", "last_error_message", "updated_at"])

        _continue_processing(mail, parsed=None, result=IntakeRunResult(), actor=request.user)
        mail.refresh_from_db()

        record_audit(
            action=AuditAction.MAIL_REPROCESSED, entity=mail, actor=request.user,
            new_value=mail.processing_status,
            remarks=serializer.validated_data.get("remarks", ""),
            metadata={"source": "USER"}, request=request,
        )
        return ok(MailIntakeDetailSerializer(mail).data)

    @action(detail=True, methods=["post"], url_path="confirm-classification")
    def confirm_classification(self, request, unique_id=None):
        """Confirm the classification and create the real work item.

        This is the only route by which an email becomes a Bug.
        """
        from apps.masters.models import (
            ModuleMaster,
            PriorityMaster,
            ProjectMaster,
            SeverityMaster,
            SubmoduleMaster,
        )
        from apps.tickets.services.ticket_service import confirm_classification as confirm

        mail = self.get_object()
        if mail.linked_ticket_id is None:
            raise ValidationError({"detail": ["This mail has no ticket to confirm."]})

        serializer = ConfirmClassificationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        def resolve(model, key):
            value = data.get(key)
            if not value:
                return None
            instance = model.objects.filter(unique_id=value, is_deleted=False).first()
            if instance is None:
                raise ValidationError({key: ["No such record."]})
            return instance

        User = request.user.__class__
        owner = None
        if data.get("owner"):
            owner = User.objects.filter(unique_id=data["owner"], is_active=True).first()
            if owner is None:
                raise ValidationError({"owner": ["No such active user."]})

        with transaction.atomic():
            # Locked so two reviewers cannot both confirm and create two bugs.
            ticket = (
                mail.linked_ticket.__class__.objects.select_for_update()
                .get(pk=mail.linked_ticket_id)
            )
            if ticket.bug_id:
                raise ValidationError(
                    {"detail": [f"Ticket {ticket.reference} is already confirmed."]}
                )

            ticket = confirm(
                ticket=ticket,
                actor=request.user,
                ticket_type=data["ticket_type"],
                title=data.get("title") or None,
                description=data.get("description"),
                project=resolve(ProjectMaster, "project"),
                module=resolve(ModuleMaster, "module"),
                submodule=resolve(SubmoduleMaster, "submodule"),
                priority=resolve(PriorityMaster, "priority"),
                severity=resolve(SeverityMaster, "severity"),
                owner=owner,
                environment=data.get("environment") or None,
                expected_closure_date=data.get("expected_closure_date"),
                remarks=data.get("remarks", ""),
                request=request,
            )
            self._record_human_classification(mail=mail, ticket=ticket, actor=request.user)

            from apps.mail_intake.services.history_service import transition

            transition(
                mail=mail,
                to_status=MailProcessingStatus.TICKET_CREATED,
                action="CLASSIFICATION_CONFIRMED",
                remarks=f"Confirmed as {ticket.ticket_type}.",
                actor=request.user,
                processed=True,
            )

        mail.refresh_from_db()
        return ok(
            {
                "mail": MailIntakeDetailSerializer(mail).data,
                "ticket_no": ticket.reference,
                "bug_id": str(ticket.bug.unique_id) if ticket.bug_id else None,
                "bug_no": ticket.bug.bug_no if ticket.bug_id else "",
            }
        )

    @action(detail=True, methods=["post"])
    def ignore(self, request, unique_id=None):
        """Discard a message without creating work, keeping the record."""
        from apps.mail_intake.services.history_service import transition

        mail = self.get_object()
        serializer = IgnoreSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        if mail.linked_ticket_id:
            raise ValidationError(
                {"detail": ["This mail already has a ticket and cannot be ignored."]}
            )

        transition(
            mail=mail, to_status=MailProcessingStatus.REJECTED, action="IGNORED",
            remarks=serializer.validated_data["reason"], actor=request.user,
            processed=True,
        )
        record_audit(
            action=AuditAction.MAIL_IGNORED, entity=mail, actor=request.user,
            remarks=serializer.validated_data["reason"],
            metadata={"source": "USER"}, request=request,
        )
        mail.refresh_from_db()
        return ok(MailIntakeDetailSerializer(mail).data)

    @action(detail=False, methods=["post"], url_path="process-now")
    def process_now(self, request):
        """Trigger a fetch on demand.

        Guarded by the same advisory lock the cron command takes, because this
        is the one endpoint a human can click repeatedly (spec 34).
        """
        from django.conf import settings

        from apps.mail_intake.services.intake_orchestrator import run_intake

        if not getattr(settings, "MAIL_INTAKE_ENABLED", False):
            raise ValidationError({"detail": ["Mail intake is disabled."]})

        with advisory_lock(RUN_LOCK) as acquired:
            if not acquired:
                raise ValidationError({"detail": ["A mail fetch is already running."]})
            try:
                result = run_intake(actor=request.user)
            except Exception as exc:
                # Never leak a stack trace or a credential to the client.
                logger.exception("Manual mail fetch failed")
                raise ValidationError(
                    {"detail": ["Mail fetch failed. See the server log for details."]}
                ) from exc

        return ok(result.as_dict())

    def _record_human_classification(self, *, mail, ticket, actor):
        """Close the loop on the classification audit row (spec 33)."""
        audit = mail.classification_audits.order_by("-id").first()
        if audit is None:
            return
        audit.ticket = ticket
        audit.human_final_type = ticket.ticket_type
        audit.human_final_project = ticket.project
        audit.human_final_module = ticket.module
        audit.corrected_by = actor
        audit.corrected_at = timezone.now()
        audit.save(
            update_fields=[
                "ticket", "human_final_type", "human_final_project",
                "human_final_module", "corrected_by", "corrected_at",
            ]
        )
