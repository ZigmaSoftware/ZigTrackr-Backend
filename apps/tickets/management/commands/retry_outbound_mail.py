"""Explicitly retry a failed requester notice after fixing SMTP."""

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from apps.tickets.models import OutboundMail


class Command(BaseCommand):
    help = "Requeue one FAILED requester email by its outbound-mail ID."

    def add_arguments(self, parser):
        parser.add_argument("job_id", type=int)

    def handle(self, *args, **options):
        with transaction.atomic():
            job = OutboundMail.objects.select_for_update().filter(pk=options["job_id"]).first()
            if job is None or job.status != OutboundMail.Status.FAILED:
                raise CommandError("Job does not exist or is not FAILED.")
            job.status = OutboundMail.Status.RETRY
            job.attempts = 0
            job.next_attempt_at = timezone.now()
            job.claimed_at = None
            job.last_error = ""
            job.save(update_fields=["status", "attempts", "next_attempt_at", "claimed_at", "last_error"])
        self.stdout.write(self.style.SUCCESS(
            f"Outbound mail {job.pk} will be picked up by the next recovery sweep."
        ))
