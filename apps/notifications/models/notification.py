"""In-app notifications (spec 52)."""

import uuid

from django.conf import settings
from django.db import models


class NotificationType(models.TextChoices):
    BUG_ASSIGNED = "BUG_ASSIGNED", "Bug Assigned to You"
    PRIORITY_CRITICAL = "PRIORITY_CRITICAL", "Priority Changed to Critical"
    EXPECTED_CLOSURE_CHANGED = "EXPECTED_CLOSURE_CHANGED", "Expected Closure Changed"
    MOVED_TO_TESTING = "MOVED_TO_TESTING", "Bug Moved to Testing"
    TEST_FAILED = "TEST_FAILED", "Test Failed"
    BUG_REOPENED = "BUG_REOPENED", "Bug Reopened"
    BUG_OVERDUE = "BUG_OVERDUE", "Bug Overdue"
    UPDATE_PENDING = "UPDATE_PENDING", "Daily Update Pending"
    # Ticket-side types. The original set was bug-only, which left the
    # support workflow -- where most activity now happens -- unnotifiable.
    TICKET_ASSIGNED = "TICKET_ASSIGNED", "Ticket Assigned to You"
    TICKET_STATUS_CHANGED = "TICKET_STATUS_CHANGED", "Ticket Status Changed"
    TICKET_UNASSIGNED_NEW = "TICKET_UNASSIGNED_NEW", "New Unassigned Ticket"
    CHAT_MESSAGE = "CHAT_MESSAGE", "New Chat Message"


# Types that should interrupt rather than simply queue: the client shows
# these as a persistent toast instead of one that auto-dismisses.
URGENT_TYPES = frozenset({
    NotificationType.PRIORITY_CRITICAL,
    NotificationType.BUG_OVERDUE,
    NotificationType.TEST_FAILED,
})


class Notification(models.Model):
    id = models.BigAutoField(primary_key=True)
    unique_id = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)

    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name="notifications", db_column="recipient_id",
    )
    notification_type = models.CharField(max_length=40, choices=NotificationType.choices)
    title = models.CharField(max_length=200)
    message = models.TextField(blank=True, default="")

    # A notification points at a bug or a ticket, never both. Both are
    # nullable because some types (a new unassigned ticket in the queue)
    # are about a queue rather than one record the recipient owns.
    bug = models.ForeignKey(
        "bugs.Bug", on_delete=models.CASCADE, null=True, blank=True,
        related_name="notifications", db_column="bug_id",
    )
    ticket = models.ForeignKey(
        "tickets.SupportTicket", on_delete=models.CASCADE, null=True, blank=True,
        related_name="notifications", db_column="ticket_id",
    )
    link = models.CharField(max_length=300, blank=True, default="")

    is_read = models.BooleanField(default=False)
    read_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="sent_notifications", db_column="created_by",
    )

    class Meta:
        db_table = "notifications"
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["recipient", "is_read", "-created_at"], name="idx_notif_recipient"),
            models.Index(fields=["bug"], name="idx_notif_bug"),
            models.Index(fields=["ticket"], name="idx_notif_ticket"),
        ]

    def __str__(self):
        return f"{self.notification_type} -> {self.recipient_id}"
