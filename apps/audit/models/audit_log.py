"""Audit log (spec 41)."""

import uuid

from django.conf import settings
from django.db import models


class AuditAction(models.TextChoices):
    BUG_CREATED = "BUG_CREATED", "Bug Created"
    BUG_UPDATED = "BUG_UPDATED", "Bug Updated"
    ASSIGN = "ASSIGN", "Assigned"
    REASSIGN = "REASSIGN", "Reassigned"
    STATUS_CHANGE = "STATUS_CHANGE", "Status Changed"
    PRIORITY_CHANGE = "PRIORITY_CHANGE", "Priority Changed"
    SEVERITY_CHANGE = "SEVERITY_CHANGE", "Severity Changed"
    EXPECTED_CLOSURE_CHANGE = "EXPECTED_CLOSURE_CHANGE", "Expected Closure Changed"
    UPDATE_ADDED = "UPDATE_ADDED", "Daily Update Added"
    ROOT_CAUSE_UPDATE = "ROOT_CAUSE_UPDATE", "Root Cause Updated"
    RESOLUTION_UPDATE = "RESOLUTION_UPDATE", "Resolution Updated"
    TESTING_RESULT = "TESTING_RESULT", "Testing Result Recorded"
    RESOLVED = "RESOLVED", "Resolved"
    CLOSURE = "CLOSURE", "Closed"
    REOPEN = "REOPEN", "Reopened"
    REJECTED = "REJECTED", "Rejected"
    ATTACHMENT_UPLOAD = "ATTACHMENT_UPLOAD", "Attachment Uploaded"
    ATTACHMENT_DELETE = "ATTACHMENT_DELETE", "Attachment Deleted"
    ATTACHMENT_DOWNLOAD = "ATTACHMENT_DOWNLOAD", "Attachment Downloaded"
    USER_CREATED = "USER_CREATED", "User Created"
    USER_UPDATED = "USER_UPDATED", "User Updated"
    USER_DEACTIVATED = "USER_DEACTIVATED", "User Deactivated"
    USER_REACTIVATED = "USER_REACTIVATED", "User Reactivated"
    USER_PASSWORD_RESET = "USER_PASSWORD_RESET", "Password Reset by Admin"
    USER_ROLES_CHANGED = "USER_ROLES_CHANGED", "User Roles Changed"
    ROLE_UPDATED = "ROLE_UPDATED", "Role Updated"
    PERMISSION_CHANGED = "PERMISSION_CHANGED", "Permissions Changed"
    LOGIN = "LOGIN", "Login"
    LOGOUT = "LOGOUT", "Logout"
    PASSWORD_CHANGED = "PASSWORD_CHANGED", "Password Changed"

    # ---- MAIL INTAKE / TICKETS (spec 47) ----
    MAIL_RECEIVED = "MAIL_RECEIVED", "Mail Received"
    MAIL_REJECTED = "MAIL_REJECTED", "Mail Rejected"
    MAIL_DUPLICATE = "MAIL_DUPLICATE", "Mail Duplicate Ignored"
    MAIL_THREAD_LINKED = "MAIL_THREAD_LINKED", "Mail Linked to Ticket Thread"
    MAIL_CLASSIFIED = "MAIL_CLASSIFIED", "Mail Classified"
    MAIL_CLASSIFICATION_CORRECTED = "MAIL_CLASSIFICATION_CORRECTED", "Mail Classification Corrected"
    MAIL_CLASSIFICATION_CONFIRMED = "MAIL_CLASSIFICATION_CONFIRMED", "Mail Classification Confirmed"
    MAIL_REPROCESSED = "MAIL_REPROCESSED", "Mail Reprocessed"
    MAIL_IGNORED = "MAIL_IGNORED", "Mail Ignored"
    MAIL_ACK_SENT = "MAIL_ACK_SENT", "Acknowledgement Sent"
    TICKET_CREATED_FROM_EMAIL = "TICKET_CREATED_FROM_EMAIL", "Ticket Created from Email"
    TICKET_CREATED = "TICKET_CREATED", "Ticket Created"
    TICKET_DELETED = "TICKET_DELETED", "Ticket Deleted"
    TICKET_TYPE_CHANGED = "TICKET_TYPE_CHANGED", "Ticket Type Changed"
    MODULE_MAPPING_CHANGED = "MODULE_MAPPING_CHANGED", "Module Mapping Changed"
    TICKET_ASSIGNED = "TICKET_ASSIGNED", "Ticket Assigned"
    TICKET_UPDATE_ADDED = "TICKET_UPDATE_ADDED", "Ticket Update Added"
    ACCESS_APPROVED = "ACCESS_APPROVED", "Access Request Approved"
    ACCESS_REJECTED = "ACCESS_REJECTED", "Access Request Rejected"
    ACCESS_IMPLEMENTED = "ACCESS_IMPLEMENTED", "Access Change Implemented"
    CLASSIFICATION_RULE_CHANGED = "CLASSIFICATION_RULE_CHANGED", "Classification Rule Changed"


class AuditLog(models.Model):
    """Append-only compliance record.

    Distinct from the bug history tables: those are the user-facing narrative
    with typed foreign keys, this is the flat who-changed-what-when trail that
    spans every entity in the system.
    """

    id = models.BigAutoField(primary_key=True)
    unique_id = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)

    entity_type = models.CharField(max_length=60)
    entity_id = models.BigIntegerField(null=True, blank=True)
    entity_unique_id = models.UUIDField(null=True, blank=True)
    entity_label = models.CharField(max_length=120, blank=True, default="")

    action = models.CharField(max_length=40, choices=AuditAction.choices)
    field_name = models.CharField(max_length=60, blank=True, default="")
    old_value = models.TextField(blank=True, default="")
    new_value = models.TextField(blank=True, default="")
    remarks = models.TextField(blank=True, default="")
    metadata = models.JSONField(default=dict, blank=True)

    performed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
        related_name="audit_entries", db_column="performed_by",
    )
    performed_by_name = models.CharField(max_length=150, blank=True, default="")
    performed_at = models.DateTimeField(auto_now_add=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=500, blank=True, default="")

    class Meta:
        db_table = "audit_log"
        ordering = ["-performed_at", "-id"]
        indexes = [
            models.Index(fields=["entity_type", "entity_id"], name="idx_audit_entity"),
            models.Index(fields=["action", "performed_at"], name="idx_audit_action_time"),
            models.Index(fields=["performed_by", "performed_at"], name="idx_audit_user_time"),
            models.Index(fields=["performed_at"], name="idx_audit_time"),
        ]

    def __str__(self):
        return f"{self.action} {self.entity_type}#{self.entity_id}"
