"""Mail intake serializers.

The list/detail split follows apps/bugs/serializers/bug.py: the list screen must
not pay for nested detail it never renders.

One rule governs all of these: **raw body_html is never exposed**. It is
attacker-controlled markup from an unauthenticated sender. Only the sanitised
copy is served, and only through its own permission-gated endpoint.
"""

from rest_framework import serializers

from apps.mail_intake.models import MailAttachment, MailIntake, MailProcessingHistory


class MailAttachmentSerializer(serializers.ModelSerializer):
    id = serializers.UUIDField(source="unique_id", read_only=True)

    class Meta:
        model = MailAttachment
        fields = [
            "id", "file_name", "file_type", "file_extension", "file_size",
            "is_inline", "content_id", "is_rejected", "rejection_reason",
            "created_at",
        ]
        read_only_fields = fields


class MailProcessingHistorySerializer(serializers.ModelSerializer):
    id = serializers.UUIDField(source="unique_id", read_only=True)
    performed_by_name = serializers.SerializerMethodField()

    class Meta:
        model = MailProcessingHistory
        fields = [
            "id", "from_status", "to_status", "action", "remarks",
            "error_code", "performed_by_name", "created_at",
        ]
        read_only_fields = fields

    def get_performed_by_name(self, obj):
        # Null means the cron process acted. Saying "System" is honest; naming
        # an administrator would not be (spec 47).
        if obj.performed_by_id is None:
            return "System"
        return obj.performed_by.display_name


class TicketRefSerializer(serializers.Serializer):
    id = serializers.UUIDField(source="unique_id", read_only=True)
    ticket_no = serializers.CharField(read_only=True)
    ticket_type = serializers.CharField(read_only=True)
    status = serializers.CharField(read_only=True)
    needs_review = serializers.BooleanField(read_only=True)


class MailIntakeListSerializer(serializers.ModelSerializer):
    id = serializers.UUIDField(source="unique_id", read_only=True)
    # reference, not ticket_no: a mail whose ticket is still awaiting review has
    # only an intake ref, and a blank column here would look like a broken link.
    ticket_no = serializers.CharField(source="linked_ticket.reference", default="", read_only=True)
    ticket_type = serializers.CharField(source="linked_ticket.ticket_type", default="", read_only=True)
    classification_score = serializers.IntegerField(
        source="linked_ticket.classification_score", default=None, read_only=True,
    )
    needs_review = serializers.BooleanField(
        source="linked_ticket.needs_review", default=False, read_only=True,
    )
    band = serializers.SerializerMethodField()
    mapped_module = serializers.SerializerMethodField()
    has_attachments = serializers.SerializerMethodField()

    class Meta:
        model = MailIntake
        fields = [
            "id", "received_at", "from_email", "from_name", "subject",
            "processing_status", "ticket_no", "ticket_type",
            "classification_score", "band", "needs_review", "mapped_module",
            "has_attachments", "is_duplicate", "is_auto_reply", "is_bounce",
            "is_thread_reply", "processing_attempts", "last_error_code",
        ]
        read_only_fields = fields

    def get_band(self, obj):
        """The confidence band, computed server-side.

        The frontend must not re-derive `score >= 80`: that would scatter a
        threshold the settings deliberately centralise (spec 19).
        """
        ticket = obj.linked_ticket
        if ticket is None or ticket.classification_score is None:
            return "UNKNOWN"
        if ticket.ticket_type == "UNKNOWN":
            return "UNKNOWN"
        return "REVIEW" if ticket.needs_review else "AUTO"

    def get_mapped_module(self, obj):
        ticket = obj.linked_ticket
        if ticket is None:
            return ""
        parts = [p.name for p in (ticket.project, ticket.module, ticket.submodule) if p]
        return " / ".join(parts)

    def get_has_attachments(self, obj):
        return bool(getattr(obj, "attachment_count", 0) or obj.attachments.exists())


class MailIntakeDetailSerializer(MailIntakeListSerializer):
    attachments = MailAttachmentSerializer(many=True, read_only=True)
    linked_ticket = TicketRefSerializer(read_only=True)

    class Meta(MailIntakeListSerializer.Meta):
        fields = MailIntakeListSerializer.Meta.fields + [
            # body_text only. body_html is deliberately absent: the sanitised
            # copy is served by the separate, gated /body/ endpoint.
            "body_text", "normalized_subject", "normalized_body",
            "to_emails", "cc_emails", "reply_to_email", "message_id",
            "in_reply_to", "mailbox_folder", "raw_size_bytes",
            "last_error_message", "processed_at", "created_at", "updated_at",
            "attachments", "linked_ticket",
        ]
        read_only_fields = fields


class MailBodySerializer(serializers.Serializer):
    """The sanitised HTML body, for rendering inside a sandboxed iframe."""

    html = serializers.CharField(read_only=True)
    text = serializers.CharField(read_only=True)
    has_html = serializers.BooleanField(read_only=True)


class ConfirmClassificationSerializer(serializers.Serializer):
    """Payload for turning a reviewed ticket into real work.

    Project, priority and severity are conditionally required: a BUG cannot be
    created without them, while a SERVICE_REQUEST must not be asked for a
    severity. Enforced in validate() rather than with required=True for that
    reason.
    """

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
                raise serializers.ValidationError(
                    {
                        field: ["This field is required when creating a bug."]
                        for field in missing
                    }
                )
        return attrs


class ReprocessSerializer(serializers.Serializer):
    remarks = serializers.CharField(required=False, allow_blank=True)


class IgnoreSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=255)
