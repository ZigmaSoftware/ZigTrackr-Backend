"""Support ticket API (spec 36).

Controlled actions only -- spec 36 forbids one unrestricted update endpoint.

Two gates apply to every mutating action, never one:
  * the codename decides whether the action exists for this user;
  * can_mutate_ticket() decides which rows they may apply it to.
This mirrors BugViewSet, where the same belt-and-suspenders pairing is spelled
out in a comment for the same reason.
"""

from django.db import transaction
from django.db.models import Count, DateTimeField, IntegerField, OuterRef, Q, Subquery, Value
from django.db.models.functions import Coalesce
from rest_framework import status as http_status
from rest_framework import serializers, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import IsAuthenticated

from apps.audit.models import AuditAction
from apps.bugs.models import Bug
from apps.tickets.constants import TicketStatus, TicketType, TicketUpdateSource
from apps.tickets.filters import TicketFilter
from apps.tickets.models import SupportTicket, TicketChatMessage
from apps.tickets.serializers import (
    AddTicketUpdateSerializer,
    ApprovalSerializer,
    AssignTicketSerializer,
    CreateTicketSerializer,
    ReviewTicketSerializer,
    ReassignTicketSerializer,
    SupportTicketDetailSerializer,
    SupportTicketListSerializer,
    TicketChatInboxSerializer,
    TicketWorkflowSerializer,
    WorkTransitionSerializer,
)
from apps.tickets.selectors import base_ticket_queryset
from common.permissions.require import RequirePermission, has_permission
from common.permissions.scoping import can_mutate_ticket, scope_bug_queryset, scope_ticket_queryset
from common.responses import EnvelopeMessageMixin, ok
from common.services.audit import record_audit
from common.viewsets import PermissionByActionMixin


class ChatReceiptSerializer(serializers.Serializer):
    message_ids = serializers.ListField(child=serializers.UUIDField(), allow_empty=False, max_length=200)
    status = serializers.ChoiceField(choices=("delivered", "read"))


class SupportTicketViewSet(PermissionByActionMixin, EnvelopeMessageMixin,
                           viewsets.ModelViewSet):
    lookup_field = "unique_id"
    lookup_url_kwarg = "unique_id"
    permission_classes = [IsAuthenticated]
    # Must list every key the UI marks sortable: OrderingFilter silently
    # ignores anything absent here, so the column would look sortable and do
    # nothing. age_days maps to created_at -- older ticket, larger age -- so the
    # two sort in opposite directions.
    ordering_fields = [
        "ticket_no", "ref_no", "created_at", "status", "ticket_type", "title",
        "expected_closure_date", "age_days", "original_mail_received_at",
    ]
    ordering = ["-id"]
    filterset_class = TicketFilter

    permission_map = {
        "list": [RequirePermission("tickets.ticket.view")],
        "retrieve": [RequirePermission("tickets.ticket.view")],
        "create": [RequirePermission("tickets.ticket.create")],
        "update": [RequirePermission("tickets.ticket.update")],
        "partial_update": [RequirePermission("tickets.ticket.update")],
        "destroy": [RequirePermission("tickets.ticket.delete")],
        "updates": [RequirePermission("tickets.ticket.view")],
        "timeline": [RequirePermission("tickets.ticket.view")],
        "chat_messages": [RequirePermission("tickets.ticket.view")],
        "chat_inbox": [RequirePermission("tickets.ticket.view")],
        "chat_message_action": [RequirePermission("tickets.ticket.view")],
        "chat_receipts": [RequirePermission("tickets.ticket.view")],
        "add_update": [RequirePermission("tickets.ticket.add_update")],
        "work_transition": [RequirePermission("tickets.ticket.add_update")],
        "review": [RequirePermission("tickets.ticket.classify")],
        "assign": [RequirePermission("tickets.ticket.assign")],
        "reassign": [RequirePermission("tickets.ticket.reassign")],
        "start": [RequirePermission("tickets.ticket.add_update")],
        "complete": [RequirePermission("tickets.ticket.add_update")],
        "close": [RequirePermission("tickets.ticket.add_update")],
        "approve": [RequirePermission("access.request.approve")],
        "reject": [RequirePermission("access.request.reject")],
        "implement": [RequirePermission("access.request.implement")],
    }

    envelope_messages = {
        "create": "Ticket created.",
        "add_update": "Update added.",
        "review": "Ticket reviewed.",
        "assign": "Ticket assigned.",
        "reassign": "Ticket reassigned.",
        "start": "Service request started.",
        "complete": "Service request completed.",
        "close": "Ticket closed.",
        "approve": "Access request approved.",
        "reject": "Access request rejected.",
        "implement": "Access change recorded.",
    }

    def get_queryset(self):
        queryset = base_ticket_queryset()
        return scope_ticket_queryset(queryset, self.request.user)

    def get_serializer_class(self):
        if self.action == "create":
            return CreateTicketSerializer
        if self.action == "list":
            return SupportTicketListSerializer
        if self.action == "chat_inbox":
            return TicketChatInboxSerializer
        return SupportTicketDetailSerializer

    def _get_for_write(self, request):
        ticket = self.get_object()
        if not can_mutate_ticket(request.user, ticket):
            raise PermissionDenied("You do not have permission to modify this ticket.")
        return ticket

    # ---- CRUD ----

    def create(self, request, *args, **kwargs):
        from apps.tickets.services.outbound_mail import queue_acknowledgement
        from apps.tickets.services.ticket_service import create_manual_ticket

        serializer = CreateTicketSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        requester = request.user
        if data.get("requester"):
            User = request.user.__class__
            requester = User.objects.filter(
                unique_id=data["requester"], is_active=True
            ).first()
            if requester is None:
                raise ValidationError({"requester": ["No such active user."]})
        with transaction.atomic():
            ticket = create_manual_ticket(
                actor=request.user,
                requester=requester,
                ticket_type=data["ticket_type"],
                title=data["title"],
                description=data["description"],
                source=data["source"],
                reported_by_email=data.get("reported_by_email", ""),
                reported_by_name=data.get("reported_by_name", ""),
                request=request,
            )
            # Only an explicitly supplied address receives an acknowledgement.
            if data.get("reported_by_email"):
                queue_acknowledgement(ticket=ticket, to_email=ticket.reported_by_email)

        ticket = base_ticket_queryset().get(pk=ticket.pk)
        return ok(
            SupportTicketDetailSerializer(ticket, context={"request": request}).data,
            status=http_status.HTTP_201_CREATED,
            message="Ticket created.",
        )

    def update(self, request, *args, **kwargs):
        raise ValidationError({"detail": ["Use the named ticket workflow actions."]})

    def partial_update(self, request, *args, **kwargs):
        raise ValidationError({"detail": ["Use the named ticket workflow actions."]})

    def destroy(self, request, *args, **kwargs):
        ticket = self._get_for_write(request)
        if ticket.owner_id is not None or ticket.bug_id is not None or ticket.ticket_no:
            raise ValidationError({"detail": ["Only unassigned tickets can be deleted."]})
        with transaction.atomic():
            # Recheck after locking: another worker may have assigned this
            # ticket between the detail read and the deletion request.
            ticket = SupportTicket.objects.select_for_update().get(pk=ticket.pk)
            if ticket.is_deleted or ticket.owner_id is not None or ticket.bug_id is not None or ticket.ticket_no:
                raise ValidationError({"detail": ["Only unassigned tickets can be deleted."]})
            ticket.soft_delete(deleted_by=request.user.unique_id)
            record_audit(
                action=AuditAction.TICKET_DELETED,
                entity=ticket,
                actor=request.user,
                old_value=ticket.reference,
                remarks="Unassigned ticket removed from the review queue.",
                request=request,
            )
        return ok(None, message="Ticket deleted.")

    # ---- UPDATES ----

    @action(detail=False, methods=["get"], url_path="chat/inbox")
    def chat_inbox(self, request):
        """A scoped, paginated conversation list with one preview per ticket."""
        latest = TicketChatMessage.objects.filter(ticket=OuterRef("pk")).order_by("-created_at", "-pk")
        unread = (
            TicketChatMessage.objects.filter(
                ticket=OuterRef("pk"), sender_type="REQUESTER",
                read_at__isnull=True, is_deleted=False,
            ).order_by().values("ticket").annotate(total=Count("pk")).values("total")[:1]
        )
        visible_bugs = scope_bug_queryset(Bug.objects.all(), request.user).values("pk")
        queryset = self.get_queryset().filter(owner__isnull=False).filter(
            Q(bug__isnull=True) | Q(bug__pk__in=visible_bugs)
        ).annotate(
            inbox_last_at=Subquery(latest.values("created_at")[:1], output_field=DateTimeField()),
            inbox_last_text=Subquery(latest.values("message_text")[:1]),
            inbox_last_sender=Subquery(latest.values("sender_type")[:1]),
            inbox_last_sender_name=Subquery(latest.values("sender_display_name")[:1]),
            inbox_last_sender_user_id=Subquery(latest.values("sender_user_id")[:1]),
            inbox_last_deleted=Subquery(latest.values("is_deleted")[:1]),
            inbox_unread_count=Coalesce(
                Subquery(unread, output_field=IntegerField()), Value(0), output_field=IntegerField(),
            ),
        )
        search = request.query_params.get("search", "").strip()
        if search:
            queryset = queryset.filter(
                Q(title__icontains=search) | Q(ticket_no__icontains=search)
                | Q(ref_no__icontains=search) | Q(reported_by_name__icontains=search)
                | Q(reported_by_email__icontains=search)
            )
        view_filter = request.query_params.get("filter", "all")
        if view_filter == "mine":
            queryset = queryset.filter(owner=request.user)
        elif view_filter == "unread":
            queryset = queryset.filter(inbox_unread_count__gt=0)
        elif view_filter == "closed":
            terminal = ("CLOSED", "REJECTED")
            queryset = queryset.filter(
                Q(bug__isnull=False, bug__status__in=terminal)
                | Q(bug__isnull=True, status__in=terminal)
            )
        elif view_filter != "all":
            raise ValidationError({"filter": ["Choose all, mine, unread, or closed."]})
        queryset = queryset.order_by(Coalesce("inbox_last_at", "created_at").desc(), "-pk")
        page = self.paginate_queryset(queryset)
        rows = list(page if page is not None else queryset)
        from apps.accounts.services.role_labels import role_labels_for_users

        role_labels = role_labels_for_users(
            row.inbox_last_sender_user_id for row in rows
            if row.inbox_last_sender == "STAFF" and row.inbox_last_sender_user_id
        )
        serializer = TicketChatInboxSerializer(
            rows, many=True, context={"request": request, "role_labels": role_labels},
        )
        return self.get_paginated_response(serializer.data) if page is not None else ok(serializer.data)

    @action(detail=True, methods=["get"])
    def updates(self, request, unique_id=None):
        from apps.tickets.serializers import TicketUpdateSerializer

        ticket = self.get_object()
        return ok(TicketUpdateSerializer(ticket.updates.all(), many=True).data)

    @action(detail=True, methods=["post"], url_path="work-transition")
    def work_transition(self, request, unique_id=None):
        """Start / hold / rectify / close, for every ticket type."""
        from apps.tickets.services.ticket_service import transition_work

        ticket = self._get_for_write(request)
        serializer = WorkTransitionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        to_status = serializer.validated_data["to_status"]
        remarks = serializer.validated_data.get("remarks", "")

        # Closing is the tester's call, not the developer's, so it carries its
        # own permission rather than riding on add_update.
        if to_status == TicketStatus.CLOSED and not has_permission(request.user, "tickets.ticket.verify_close"):
            raise PermissionDenied("Only a tester or lead may close a verified ticket.")
        current = ticket.bug.status if ticket.bug_id else ticket.status
        if (current == TicketStatus.REOPENED and to_status == TicketStatus.IN_PROGRESS
                and not has_permission(request.user, "tickets.ticket.verify_close")):
            raise PermissionDenied("A tester must review a reopened ticket before development resumes.")

        with transaction.atomic():
            updated = transition_work(
                ticket=ticket, actor=request.user, to_status=to_status,
                remarks=remarks, payload={
                    "root_cause": serializer.validated_data.get("root_cause"),
                    "resolution": serializer.validated_data.get("resolution"),
                }, request=request,
            )
            if to_status == TicketStatus.CLOSED:
                from apps.tickets.services.outbound_mail import queue_closed_notice
                queue_closed_notice(ticket=updated, remarks=remarks)

        updated = base_ticket_queryset().get(pk=updated.pk)
        return ok(SupportTicketDetailSerializer(updated).data, message="Ticket updated.")

    @action(detail=True, methods=["get"])
    def timeline(self, request, unique_id=None):
        from apps.tickets.services.timeline_service import build_ticket_timeline

        ticket = self.get_object()
        return ok(build_ticket_timeline(ticket), message="Timeline retrieved.")

    @action(detail=True, methods=["get", "post"], url_path="chat/messages")
    def chat_messages(self, request, unique_id=None):
        from apps.tickets.services.chat_service import message_role_labels, send_message, serialize_message

        ticket = self.get_object()
        if request.method == "GET":
            rows = list(ticket.chat_messages.select_related("reply_to_message").order_by("created_at", "id"))
            role_labels = message_role_labels(rows)
            return ok([serialize_message(row, user=request.user, role_labels=role_labels) for row in rows])
        text = request.data.get("message", "")
        if not isinstance(text, str):
            raise ValidationError({"message": ["Enter a message."]})
        row = send_message(ticket=ticket, text=text, user=request.user,
                           reply_to_id=request.data.get("reply_to"))
        return ok(serialize_message(row, user=request.user), message="Message sent.")

    @action(detail=True, methods=["post"], url_path=r"chat/messages/(?P<message_id>[^/.]+)")
    def chat_message_action(self, request, unique_id=None, message_id=None):
        from apps.tickets.services.chat_service import change_message, serialize_message

        ticket = self.get_object()
        action_name = request.data.get("action")
        row = change_message(ticket=ticket, message_id=message_id,
                             action=action_name, user=request.user,
                             text=request.data.get("message"), emoji=request.data.get("emoji"))
        return ok(serialize_message(row, user=request.user))

    @action(detail=True, methods=["post"], url_path="chat/receipts")
    def chat_receipts(self, request, unique_id=None):
        from apps.tickets.services.chat_service import acknowledge_messages

        serializer = ChatReceiptSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        ticket = self.get_object()
        return ok(acknowledge_messages(ticket=ticket, user=request.user,
                                       **serializer.validated_data))

    @action(detail=True, methods=["post"], url_path="add-update")
    def add_update(self, request, unique_id=None):
        from apps.tickets.services.ticket_service import add_ticket_update

        ticket = self._get_for_write(request)
        serializer = AddTicketUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        add_ticket_update(
            ticket=ticket,
            update_text=serializer.validated_data["update_text"],
            remarks=serializer.validated_data.get("remarks", ""),
            actor=request.user,
            source=TicketUpdateSource.USER,
            request=request,
        )
        return ok(SupportTicketDetailSerializer(ticket).data)

    # ---- ASSIGNMENT ----

    @action(detail=True, methods=["post"])
    def review(self, request, unique_id=None):
        from apps.masters.models import (
            ModuleMaster,
            PriorityMaster,
            ProjectMaster,
            SeverityMaster,
            SubmoduleMaster,
        )
        from apps.tickets.services.ticket_service import review_ticket

        ticket = self._get_for_write(request)
        serializer = ReviewTicketSerializer(data=request.data)
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

        owner = None
        if data.get("owner"):
            User = request.user.__class__
            owner = User.objects.filter(unique_id=data["owner"], is_active=True).first()
            if owner is None:
                raise ValidationError({"owner": ["No such active user."]})

        was_unassigned = not ticket.owner_id
        with transaction.atomic():
            locked = SupportTicket.objects.select_for_update().get(pk=ticket.pk)
            reviewed = review_ticket(
                ticket=locked,
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
            if was_unassigned and reviewed.owner_id and reviewed.ticket_no:
                from apps.tickets.services.outbound_mail import queue_assigned_notice
                queue_assigned_notice(ticket=reviewed)

        reviewed = base_ticket_queryset().get(pk=reviewed.pk)
        return ok(SupportTicketDetailSerializer(reviewed).data)

    @action(detail=True, methods=["post"])
    def assign(self, request, unique_id=None):
        with transaction.atomic():
            current = self._get_for_write(request)
            locked = SupportTicket.objects.select_for_update().get(pk=current.pk)
            was_unassigned = not locked.owner_id
            response = self._assign(request, AuditAction.TICKET_ASSIGNED)
            if was_unassigned:
                from apps.tickets.services.outbound_mail import queue_assigned_notice
                ticket = SupportTicket.objects.get(unique_id=unique_id)
                queue_assigned_notice(ticket=ticket)
        return response

    @action(detail=True, methods=["post"])
    def reassign(self, request, unique_id=None):
        from apps.masters.models import (
            ModuleMaster, PriorityMaster, ProjectMaster, SeverityMaster, SubmoduleMaster,
        )
        from apps.tickets.services.assignment_service import reassign_ticket

        ticket = self._get_for_write(request)
        serializer = ReassignTicketSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        User = request.user.__class__
        owner = User.objects.filter(
            unique_id=data["owner"], is_active=True,
            user_roles__is_active=True, user_roles__role__code="DEVELOPER",
        ).first()
        if owner is None:
            raise ValidationError({"owner": ["Choose an active developer."]})

        fields = {
            "project": ProjectMaster,
            "module": ModuleMaster,
            "submodule": SubmoduleMaster,
            "priority": PriorityMaster,
            "severity": SeverityMaster,
        }
        routing = {}
        for key, model in fields.items():
            if key in data:
                row = model.objects.filter(unique_id=data[key], is_deleted=False).first()
                if row is None:
                    raise ValidationError({key: ["No such active record."]})
                routing[key] = row
        if "expected_closure_date" in data:
            routing["expected_closure_date"] = data["expected_closure_date"]
        if "severity" in routing and not ticket.bug_id:
            raise ValidationError({"severity": ["Severity applies only to bug tickets."]})

        updated = reassign_ticket(
            ticket=ticket, new_owner=owner, actor=request.user,
            reason=data["reason"], routing=routing, request=request,
        )
        return ok(SupportTicketDetailSerializer(base_ticket_queryset().get(pk=updated.pk)).data)

    @transaction.atomic
    def _assign(self, request, audit_action):
        from apps.tickets.models import TicketAssignmentHistory
        from apps.tickets.services.activity_service import record_activity

        ticket = self._get_for_write(request)
        if ticket.needs_review:
            raise ValidationError({"detail": ["Confirm the ticket classification before assignment."]})
        serializer = AssignTicketSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        User = request.user.__class__
        owner = User.objects.filter(
            unique_id=serializer.validated_data["owner"], is_active=True
        ).first()
        if owner is None:
            raise ValidationError({"owner": ["No such active user."]})

        if not ticket.ticket_no:
            from apps.tickets.services.ticket_number import generate_ticket_no
            from common.utils.dates import local_today

            ticket.ticket_no = generate_ticket_no(local_today())
            ticket.save(update_fields=["ticket_no", "updated_at"])

        if ticket.ticket_type == TicketType.BUG:
            if not ticket.bug_id:
                raise ValidationError({"detail": ["Review the bug ticket before assignment."]})
            from apps.bugs.services.assignment_service import assign_bug

            bug = assign_bug(
                bug=ticket.bug,
                new_owner=owner,
                actor=request.user,
                remarks=serializer.validated_data.get("remarks", ""),
                expected_closure_date=serializer.validated_data.get("expected_closure_date"),
                request=request,
            )
            previous = ticket.owner
            ticket.owner = owner
            ticket.status = TicketStatus.ASSIGNED
            ticket.expected_closure_date = bug.expected_closure_date
            ticket.updated_by = getattr(request.user, "unique_id", None)
            ticket.save(update_fields=[
                "owner", "status", "expected_closure_date", "updated_by", "updated_at",
            ])
            record_audit(
                action=audit_action, entity=ticket, actor=request.user,
                field_name="owner", old_value=getattr(previous, "display_name", ""),
                new_value=owner.display_name,
                remarks=serializer.validated_data.get("remarks", ""),
                request=request,
            )
            TicketAssignmentHistory.objects.create(
                ticket=ticket, from_owner=previous, to_owner=owner,
                performed_by=request.user, reason=serializer.validated_data.get("remarks", ""),
            )
            ticket = base_ticket_queryset().get(pk=ticket.pk)
            return ok(SupportTicketDetailSerializer(ticket).data)

        previous = ticket.owner
        ticket.owner = owner
        update_fields = ["owner", "updated_by", "updated_at"]
        if (
            ticket.ticket_type == TicketType.SERVICE_REQUEST
            and not ticket.needs_review
            and ticket.status in (TicketStatus.NEW, TicketStatus.CONFIRMED)
        ):
            ticket.status = TicketStatus.ASSIGNED
            update_fields.append("status")
        ticket.updated_by = getattr(request.user, "unique_id", None)
        ticket.save(update_fields=update_fields)

        record_audit(
            action=audit_action, entity=ticket, actor=request.user,
            field_name="owner",
            old_value=getattr(previous, "display_name", ""),
            new_value=owner.display_name,
            remarks=serializer.validated_data.get("remarks", ""),
            request=request,
        )
        TicketAssignmentHistory.objects.create(
            ticket=ticket, from_owner=previous, to_owner=owner,
            performed_by=request.user, reason=serializer.validated_data.get("remarks", ""),
        )
        record_activity(
            ticket=ticket,
            event_type="TICKET_REASSIGNED" if previous else "TICKET_ASSIGNED",
            title="Ticket Reassigned" if previous else "Ticket Assigned",
            description=f"Assigned to {owner.display_name} by {request.user.display_name}.",
            public_description="Your request has been assigned to a support specialist.",
            actor=request.user,
        )
        return ok(SupportTicketDetailSerializer(ticket).data)

    # ---- SERVICE REQUEST WORKFLOW (spec 28) ----

    @action(detail=True, methods=["post"])
    def start(self, request, unique_id=None):
        from apps.tickets.services.ticket_service import start_service_work

        ticket = self._get_for_write(request)
        serializer = TicketWorkflowSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        start_service_work(
            ticket=ticket,
            actor=request.user,
            remarks=serializer.validated_data.get("remarks", ""),
            request=request,
        )
        ticket.refresh_from_db()
        return ok(SupportTicketDetailSerializer(ticket).data)

    @action(detail=True, methods=["post"])
    def complete(self, request, unique_id=None):
        from apps.tickets.services.ticket_service import complete_service_work

        ticket = self._get_for_write(request)
        serializer = TicketWorkflowSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        complete_service_work(
            ticket=ticket,
            actor=request.user,
            remarks=serializer.validated_data.get("remarks", ""),
            request=request,
        )
        ticket.refresh_from_db()
        return ok(SupportTicketDetailSerializer(ticket).data)

    @action(detail=True, methods=["post"])
    def close(self, request, unique_id=None):
        from apps.tickets.services.ticket_service import close_ticket
        from apps.tickets.services.outbound_mail import queue_closed_notice

        ticket = self._get_for_write(request)
        serializer = TicketWorkflowSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        remarks = serializer.validated_data.get("remarks", "")
        with transaction.atomic():
            close_ticket(ticket=ticket, actor=request.user, remarks=remarks, request=request)
            ticket.refresh_from_db()
            queue_closed_notice(ticket=ticket, remarks=remarks)
        ticket.refresh_from_db()
        return ok(SupportTicketDetailSerializer(ticket).data)

    # ---- ACCESS REQUEST WORKFLOW (spec 29) ----

    @action(detail=True, methods=["post"])
    def approve(self, request, unique_id=None):
        ticket = self._get_for_write(request)
        self._require_access_request(ticket)

        if ticket.status != TicketStatus.PENDING_APPROVAL:
            # 409-style refusal: the codename is held, the state is wrong.
            raise ValidationError(
                {"detail": [f"Only a pending request can be approved (status is {ticket.status})."]}
            )
        self._refuse_self_approval(ticket, request.user)

        serializer = ApprovalSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        ticket.status = TicketStatus.APPROVED
        ticket.updated_by = getattr(request.user, "unique_id", None)
        ticket.save(update_fields=["status", "updated_by", "updated_at"])

        record_audit(
            action=AuditAction.ACCESS_APPROVED, entity=ticket, actor=request.user,
            old_value=TicketStatus.PENDING_APPROVAL, new_value=TicketStatus.APPROVED,
            remarks=serializer.validated_data.get("remarks", ""), request=request,
        )
        from apps.tickets.services.activity_service import record_activity

        record_activity(
            ticket=ticket, event_type="ACCESS_APPROVED", title="Access request approved",
            description=f"{request.user.display_name} approved the access request.",
            public_description="Your access request has been approved.", actor=request.user,
        )
        return ok(SupportTicketDetailSerializer(ticket).data)

    @action(detail=True, methods=["post"])
    def reject(self, request, unique_id=None):
        ticket = self._get_for_write(request)
        self._require_access_request(ticket)

        if ticket.status not in (TicketStatus.PENDING_APPROVAL, TicketStatus.NEEDS_REVIEW):
            raise ValidationError({"detail": ["This request cannot be rejected now."]})
        self._refuse_self_approval(ticket, request.user)

        serializer = ApprovalSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        previous = ticket.status
        ticket.status = TicketStatus.REJECTED
        ticket.updated_by = getattr(request.user, "unique_id", None)
        ticket.save(update_fields=["status", "updated_by", "updated_at"])

        record_audit(
            action=AuditAction.ACCESS_REJECTED, entity=ticket, actor=request.user,
            old_value=previous, new_value=TicketStatus.REJECTED,
            remarks=serializer.validated_data.get("remarks", ""), request=request,
        )
        from apps.tickets.services.activity_service import record_activity

        record_activity(
            ticket=ticket, event_type="ACCESS_REJECTED", title="Access request rejected",
            description=f"{request.user.display_name} rejected the access request.",
            public_description="Your access request has been rejected.", actor=request.user,
        )
        return ok(SupportTicketDetailSerializer(ticket).data)

    @action(detail=True, methods=["post"])
    def implement(self, request, unique_id=None):
        """Record that the approved access change has been made.

        The ordering guard is the important part: no access change before an
        explicit approval (spec 29). A codename alone cannot express that, so it
        is enforced here as a state check.
        """
        ticket = self._get_for_write(request)
        self._require_access_request(ticket)

        if ticket.status != TicketStatus.APPROVED:
            raise ValidationError(
                {
                    "detail": [
                        "Access cannot be implemented before it is approved "
                        f"(status is {ticket.status})."
                    ]
                }
            )

        serializer = ApprovalSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        ticket.status = TicketStatus.COMPLETED
        ticket.updated_by = getattr(request.user, "unique_id", None)
        ticket.save(update_fields=["status", "updated_by", "updated_at"])

        record_audit(
            action=AuditAction.ACCESS_IMPLEMENTED, entity=ticket, actor=request.user,
            old_value=TicketStatus.APPROVED, new_value=TicketStatus.COMPLETED,
            remarks=serializer.validated_data.get("remarks", ""), request=request,
        )
        from apps.tickets.services.activity_service import record_activity

        record_activity(
            ticket=ticket, event_type="ACCESS_IMPLEMENTED", title="Access change completed",
            description=f"{request.user.display_name} implemented the approved access change.",
            public_description="Your approved access change has been completed.", actor=request.user,
        )
        return ok(SupportTicketDetailSerializer(ticket).data)

    @staticmethod
    def _require_access_request(ticket):
        if ticket.ticket_type != TicketType.ACCESS_REQUEST:
            raise ValidationError({"detail": ["This is not an access request."]})

    @staticmethod
    def _refuse_self_approval(ticket, user):
        """Spec 29: a requester may not approve their own request."""
        if ticket.reported_by_id == user.id:
            raise PermissionDenied("You cannot approve your own access request.")
        email = (getattr(user, "email", "") or "").strip().lower()
        if email and email == (ticket.reported_by_email or "").strip().lower():
            raise PermissionDenied("You cannot approve your own access request.")
