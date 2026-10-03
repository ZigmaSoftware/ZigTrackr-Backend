"""Hard-delete every bug and every user (development/reset only).

This exists for the one legitimate case where a full reset is actually wanted
-- standing up a clean environment with a known, small set of accounts -- and
nowhere else. It intentionally bypasses the immutability guard on history
tables (ImmutableHistoryModel.delete() raises; QuerySet.delete() does not
call it, which is the only reason this command is possible at all) and the
soft-delete-only contract on Bug and User. There is no API endpoint for this;
it is reachable only from a shell with direct database access, which is the
right amount of friction for an operation this destructive.
"""

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction

from apps.audit.models import AuditLog
from apps.authentication.models import LoginAuditLog
from apps.bugs.models import (
    Bug,
    BugAssignmentHistory,
    BugAttachment,
    BugReopenHistory,
    BugStatusHistory,
    BugTestingHistory,
    BugUpdate,
)
from apps.masters.models import BugNumberSequence
from apps.notifications.models import Notification

User = get_user_model()


class Command(BaseCommand):
    help = "DESTRUCTIVE: delete every bug, its history, and every user. Requires --yes."

    def add_arguments(self, parser):
        parser.add_argument(
            "--yes", action="store_true",
            help="Required. Without it, only a summary of what would be deleted is printed.",
        )

    def handle(self, *args, **options):
        counts = {
            "Notification": Notification.objects.count(),
            "BugAttachment": BugAttachment.objects.count(),
            "BugUpdate": BugUpdate.objects.count(),
            "BugStatusHistory": BugStatusHistory.objects.count(),
            "BugAssignmentHistory": BugAssignmentHistory.objects.count(),
            "BugTestingHistory": BugTestingHistory.objects.count(),
            "BugReopenHistory": BugReopenHistory.objects.count(),
            "Bug": Bug.objects.count(),
            "BugNumberSequence": BugNumberSequence.objects.count(),
            "AuditLog": AuditLog.objects.count(),
            "LoginAuditLog": LoginAuditLog.objects.count(),
            "User": User.objects.count(),
        }

        self.stdout.write("Rows that will be permanently deleted:")
        for model, count in counts.items():
            self.stdout.write(f"  {model:22s} {count}")

        if not options["yes"]:
            self.stdout.write(self.style.WARNING(
                "\nDry run only -- no rows deleted. Re-run with --yes to actually delete."
            ))
            return

        with transaction.atomic():
            # Children before parents, so no FK ever points at a row that is
            # about to vanish. Bulk QuerySet.delete() is used throughout
            # rather than iterating instances, because the history models
            # override the instance .delete() to always raise
            # (ImmutableRecordError) -- that guard exists to stop application
            # code from editing history, and does not apply to this
            # queryset-level bulk path, which is the one place a genuine
            # full reset is allowed to bypass it.
            Notification.objects.all().delete()
            BugAttachment.objects.all().delete()
            BugUpdate.objects.all().delete()
            BugStatusHistory.objects.all().delete()
            BugAssignmentHistory.objects.all().delete()
            BugTestingHistory.objects.all().delete()
            BugReopenHistory.objects.all().delete()
            Bug.objects.all().delete()
            BugNumberSequence.objects.all().delete()
            AuditLog.objects.all().delete()
            LoginAuditLog.objects.all().delete()
            # User.delete() is not overridden to block hard deletion (only
            # soft_delete()/restore() exist as the normal app-level path), so
            # the plain queryset delete works directly here too.
            User.objects.all().delete()

        self.stdout.write(self.style.SUCCESS(
            "All bugs, their history, and all users have been permanently deleted."
        ))
