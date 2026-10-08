"""Support ticket serializers."""

from rest_framework import serializers

from apps.accounts.serializers import UserLiteSerializer
from apps.masters.models import PriorityMaster
from apps.tickets.constants import MANUAL_TICKET_SOURCES, TicketSource, TicketStatus
from apps.tickets.models import SupportTicket, TicketAttachment, TicketUpdate


class NamedRefSerializer(serializers.Serializer):
    id = serializers.UUIDField(source="unique_id", read_only=True)
    name = serializers.CharField(read_only=True)


class CodedRefSerializer(serializers.Serializer):
    id = serializers.UUIDField(source="unique_id", read_only=True)
    code = serializers.CharField(read_only=True)
    name = serializers.CharField(read_only=True)
    color = serializers.CharField(read_only=True)
    rank = serializers.IntegerField(read_only=True)


class TicketUpdateSerializer(serializers.ModelSerializer):
    id = serializers.UUIDField(source="unique_id", read_only=True)
    created_by_name = serializers.SerializerMethodField()

    class Meta:
        model = TicketUpdate
        fields = [
            "id", "update_text", "remarks", "source",
            "created_by_name", "created_at",
        ]
        read_only_fields = fields

    def get_created_by_name(self, obj):
        if obj.created_by_user_id is None:
            return "System"
        return obj.created_by_user.display_name


class SupportTicketListSerializer(serializers.ModelSerializer):
    id = serializers.UUIDField(source="unique_id", read_only=True)
    project = serializers.SerializerMethodField()
    module = serializers.SerializerMethodField()
    priority = serializers.SerializerMethodField()
    owner = serializers.SerializerMethodField()
    reported_by = UserLiteSerializer(read_only=True)
    band = serializers.SerializerMethodField()
    bug_no = serializers.CharField(source="bug.bug_no", default="", read_only=True)
    bug_id = serializers.UUIDField(source="bug.unique_id", default=None, read_only=True)
    status_label = serializers.SerializerMethodField()
    effective_status = serializers.SerializerMethodField()
    effective_expected_closure_date = serializers.DateField(read_only=True)
    age_days = serializers.IntegerField(read_only=True)
    is_overdue = serializers.BooleanField(read_only=True)
    overdue_days = serializers.IntegerField(read_only=True)
    latest_update_at = serializers.DateTimeField(source="effective_last_update_at", read_only=True)
    current_work_started_at = serializers.DateTimeField(read_only=True)
    reference = serializers.CharField(read_only=True)
    mail_id = serializers.SerializerMethodField()
    # When the message actually arrived, which for an emailed request is not the
    # same as when we created the row. Null for anything keyed in by hand; the
    # client falls back to created_at there.
    mail_received_at = serializers.SerializerMethodField()
    mail_from_email = serializers.SerializerMethodField()
    # The mailbox the request came in on -- "via" in the UI. A message may be
    # addressed to several, but only the first identifies the intake route.
    mail_to_email = serializers.SerializerMethodField()
    can_reassign = serializers.SerializerMethodField()
    can_delete = serializers.SerializerMethodField()
    reassign_block_reason = serializers.SerializerMethodField()
    allowed_actions = serializers.SerializerMethodField()
    chat_state = serializers.SerializerMethodField()

    class Meta:
        model = SupportTicket
        fields = [
            "id", "ticket_no", "ref_no", "reference",
            "description", "mail_id", "mail_received_at",
            "mail_from_email", "mail_to_email",
            "source", "ticket_type", "status", "status_label",
            "effective_status",
            "classification_method", "classification_score",
            "classification_status", "band", "needs_review",
            "title", "project", "module", "priority", "owner", "reported_by",
            "reported_by_email", "reported_by_name", "bug_no", "bug_id",
            "expected_closure_date", "effective_expected_closure_date",
            "age_days", "is_overdue", "overdue_days", "latest_update_at", "current_work_started_at",
            "created_at", "can_reassign", "can_delete", "reassign_block_reason",
            "allowed_actions", "chat_state",
        ]
        read_only_fields = fields

    def get_mail_id(self, obj):
        mail = obj.original_mail
        return str(mail.unique_id) if mail else None

    def get_mail_received_at(self, obj):
        mail = obj.original_mail
        return serializers.DateTimeField().to_representation(mail.received_at) if mail else None

    def get_mail_from_email(self, obj):
        mail = obj.original_mail
        return mail.from_email if mail else ""

    def get_mail_to_email(self, obj):
        mail = obj.original_mail
        recipients = mail.to_emails if mail else []
        return recipients[0] if recipients else ""

    def get_project(self, obj):
        project = obj.project or getattr(obj.bug, "project", None)
        return NamedRefSerializer(project).data if project else None

    def get_module(self, obj):
        module = obj.module or getattr(obj.bug, "module", None)
        return NamedRefSerializer(module).data if module else None

    def get_priority(self, obj):
        priority = obj.priority or getattr(obj.bug, "priority", None)
        return CodedRefSerializer(priority).data if priority else None

    def get_owner(self, obj):
        owner = obj.owner or getattr(obj.bug, "owner", None)
        return UserLiteSerializer(owner).data if owner else None

    def get_effective_status(self, obj):
        return getattr(obj.bug, "status", None) or obj.status

    def get_status_label(self, obj):
        if obj.bug_id:
            return obj.bug.get_status_display()
        return obj.get_status_display()

    def get_band(self, obj):
        if obj.classification_score is None or obj.ticket_type == "UNKNOWN":
            return "UNKNOWN"
        return "REVIEW" if obj.needs_review else "AUTO"

    def get_can_reassign(self, obj):
        from apps.tickets.services.policy import can_reassign

        request = self.context.get("request")
        return can_reassign(obj, getattr(request, "user", None))

    def get_can_delete(self, obj):
        from common.permissions.require import has_permission
        from common.permissions.scoping import can_mutate_ticket

        request = self.context.get("request")
        user = getattr(request, "user", None)
        return bool(
            user and has_permission(user, "tickets.ticket.delete")
            and can_mutate_ticket(user, obj)
            and not obj.is_deleted and obj.owner_id is None
            and obj.bug_id is None and not obj.ticket_no
        )

    def get_reassign_block_reason(self, obj):
        from apps.tickets.services.policy import reassign_block_reason

        return None if self.get_can_reassign(obj) else reassign_block_reason(obj)

    def get_allowed_actions(self, obj):
        from apps.tickets.services.policy import effective_status
        from common.permissions.require import has_permission
        from common.permissions.scoping import can_mutate_ticket
        from apps.tickets.constants import WORK_TRANSITIONS

        request = self.context.get("request")
        user = getattr(request, "user", None)
        if not user or not user.is_authenticated or not can_mutate_ticket(user, obj):
            return []
        actions = []
        if self.get_can_reassign(obj):
            actions.append("REASSIGN")
        if (obj.owner_id == user.pk or has_permission(user, "tickets.ticket.mutate_all")
                or has_permission(user, "tickets.ticket.classify")):
            targets = WORK_TRANSITIONS.get(effective_status(obj), ())
            actions.extend(
                target for target in targets
                if target != TicketStatus.CLOSED
                and (target != TicketStatus.ASSIGNED or has_permission(user, "tickets.ticket.verify_close"))
            )
        return actions

    def get_chat_state(self, obj):
        from apps.tickets.services.policy import chat_state

        request = self.context.get("request")
        return chat_state(obj, getattr(request, "user", None))


class SupportTicketDetailSerializer(SupportTicketListSerializer):
    submodule = NamedRefSerializer(read_only=True)
    confirmed_by = UserLiteSerializer(read_only=True)
    updates = TicketUpdateSerializer(many=True, read_only=True)
    request_messages = serializers.SerializerMethodField()
    active_work_conflict = serializers.SerializerMethodField()

    def get_active_work_conflict(self, obj):
        from apps.accounts.models import UserRole
        from apps.tickets.services.policy import active_ticket_for_owner, effective_status

        if not obj.owner_id or effective_status(obj) not in (
            TicketStatus.NEW, TicketStatus.CONFIRMED, TicketStatus.ASSIGNED,
            TicketStatus.APPROVED, TicketStatus.PENDING, TicketStatus.ON_HOLD,
            TicketStatus.TESTING, TicketStatus.REOPENED,
        ):
            return None
        if not UserRole.objects.filter(
            user_id=obj.owner_id, is_active=True, role__code="DEVELOPER",
            role__is_active=True, role__is_deleted=False,
        ).exists():
            return None
        active = active_ticket_for_owner(obj.owner_id, exclude_ticket_id=obj.pk)
        return {"id": str(active.unique_id), "reference": active.reference} if active else None

    def get_request_messages(self, obj):
        from apps.tickets.services.conversation_service import request_messages

        return request_messages(obj)

    class Meta(SupportTicketListSerializer.Meta):
        # description, mail_id, reported_by and bug_id now come from the list
        # serializer -- the unassigned queue renders them as columns. Repeating
        # a name here would raise "duplicate field" at import time.
        fields = SupportTicketListSerializer.Meta.fields + [
            "submodule", "confirmed_by",
            "confirmed_at", "classification_reason",
            "ack_sent_at", "updates", "updated_at", "request_messages", "active_work_conflict",
        ]
        read_only_fields = fields


class TicketChatInboxSerializer(serializers.Serializer):
    id = serializers.UUIDField(source="unique_id")
    reference = serializers.CharField()
    title = serializers.CharField()
    ticket_type = serializers.CharField()
    status = serializers.SerializerMethodField()
    status_label = serializers.SerializerMethodField()
    requester_name = serializers.SerializerMethodField()
    requester_email = serializers.EmailField(source="reported_by_email")
    owner_id = serializers.UUIDField(source="owner.unique_id", allow_null=True)
    owner_name = serializers.CharField(source="owner.display_name", allow_null=True)
    last_message_at = serializers.DateTimeField(source="inbox_last_at", allow_null=True)
    last_message_text = serializers.SerializerMethodField()
    last_message_sender_type = serializers.CharField(source="inbox_last_sender", allow_null=True)
    last_message_sender_name = serializers.CharField(source="inbox_last_sender_name", allow_null=True)
    last_message_sender_role = serializers.SerializerMethodField()
    last_message_is_mine = serializers.SerializerMethodField()
    unread_count = serializers.IntegerField(source="inbox_unread_count")
    chat_state = serializers.SerializerMethodField()

    def get_status(self, obj):
        return obj.bug.status if obj.bug_id else obj.status

    def get_status_label(self, obj):
        return obj.bug.get_status_display() if obj.bug_id else obj.get_status_display()

    def get_requester_name(self, obj):
        return obj.reported_by_name or (obj.reported_by.display_name if obj.reported_by_id else obj.reported_by_email)

    def get_last_message_text(self, obj):
        return "Message deleted" if obj.inbox_last_deleted else obj.inbox_last_text

    def get_last_message_is_mine(self, obj):
        return obj.inbox_last_sender == "STAFF" and obj.inbox_last_sender_user_id == self.context["request"].user.pk

    def get_last_message_sender_role(self, obj):
        if obj.inbox_last_sender != "STAFF":
            return ""
        return self.context.get("role_labels", {}).get(obj.inbox_last_sender_user_id, "")

    def get_chat_state(self, obj):
        from apps.tickets.services.policy import chat_state

        return chat_state(obj, self.context["request"].user)


class AddTicketUpdateSerializer(serializers.Serializer):
    update_text = serializers.CharField()
    remarks = serializers.CharField(required=False, allow_blank=True)


class WorkTransitionSerializer(serializers.Serializer):
    """The shared work flow: start, hold, rectify, close."""

    to_status = serializers.ChoiceField(choices=[
        TicketStatus.ASSIGNED, TicketStatus.IN_PROGRESS, TicketStatus.ON_HOLD,
        TicketStatus.PENDING, TicketStatus.TESTING, TicketStatus.CLOSED,
    ])
    remarks = serializers.CharField(required=False, allow_blank=True, default="")
    root_cause = serializers.CharField(required=False, allow_blank=True)
    resolution = serializers.CharField(required=False, allow_blank=True)


class TicketAttachmentSerializer(serializers.ModelSerializer):
    id = serializers.UUIDField(source="unique_id", read_only=True)
    uploaded_by = UserLiteSerializer(read_only=True)
    download_url = serializers.SerializerMethodField()

    class Meta:
        model = TicketAttachment
        fields = ["id", "file_name", "file_type", "file_extension", "file_size",
                  "reason", "uploaded_by", "uploaded_at", "download_url"]

    def get_download_url(self, obj):
        # Always the gated view, never a direct MEDIA_ROOT path (spec 31).
        return f"/api/v1/tickets/attachments/{obj.unique_id}/download/"


class CreateTicketSerializer(serializers.Serializer):
    ticket_type = serializers.ChoiceField(
        choices=["BUG", "SERVICE_REQUEST", "ACCESS_REQUEST"]
    )
    title = serializers.CharField(max_length=255)
    description = serializers.CharField()
    source = serializers.ChoiceField(
        choices=[source.value for source in MANUAL_TICKET_SOURCES],
        required=False,
        default=TicketSource.MANUAL,
    )
    requester = serializers.UUIDField(required=False, allow_null=True)
    reported_by_email = serializers.EmailField(required=False, allow_blank=True)
    reported_by_name = serializers.CharField(required=False, allow_blank=True, max_length=150)


class AssignTicketSerializer(serializers.Serializer):
    owner = serializers.UUIDField()
    remarks = serializers.CharField(required=False, allow_blank=True)
    expected_closure_date = serializers.DateField(required=False, allow_null=True)


class ReassignTicketSerializer(serializers.Serializer):
    owner = serializers.UUIDField()
    reason = serializers.CharField(allow_blank=False, trim_whitespace=True)
    project = serializers.UUIDField(required=False)
    module = serializers.UUIDField(required=False)
    submodule = serializers.UUIDField(required=False)
    priority = serializers.UUIDField(required=False)
    severity = serializers.UUIDField(required=False)
    expected_closure_date = serializers.DateField(required=False, allow_null=True)


class ReviewTicketSerializer(serializers.Serializer):
    ticket_type = serializers.ChoiceField(
        choices=["BUG", "SERVICE_REQUEST", "ACCESS_REQUEST"]
    )
    title = serializers.CharField(max_length=255, required=False, allow_blank=True)
    description = serializers.CharField(required=False, allow_blank=True)
    project = serializers.UUIDField(required=False, allow_null=True)
    module = serializers.UUIDField(required=False, allow_null=True)
    submodule = serializers.UUIDField(required=False, allow_null=True)
    priority = serializers.UUIDField(required=False, allow_null=True)
    severity = serializers.UUIDField(required=False, allow_null=True)
    owner = serializers.UUIDField(required=False, allow_null=True)
    environment = serializers.CharField(required=False, allow_blank=True)
    expected_closure_date = serializers.DateField(required=False, allow_null=True)
    remarks = serializers.CharField(required=False, allow_blank=True)

    def validate(self, attrs):
        if attrs.get("ticket_type") == "BUG":
            missing = [
                field for field in ("project", "priority", "severity")
                if not attrs.get(field)
            ]
            if missing:
                raise serializers.ValidationError({
                    field: ["This field is required for bug review."]
                    for field in missing
                })
        return attrs


class ApprovalSerializer(serializers.Serializer):
    remarks = serializers.CharField(required=False, allow_blank=True)


class TicketWorkflowSerializer(serializers.Serializer):
    remarks = serializers.CharField(required=False, allow_blank=True)
