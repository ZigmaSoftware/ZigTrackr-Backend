"""Fetch and process support mail manually or during recovery.

Production polling is scheduled by Celery Beat. This command remains the
operator escape hatch for dry runs, retries, controlled backfills, and
troubleshooting.

"Nothing to do" and "another run holds the lock" are both successful outcomes.
Only genuine misconfiguration is reported as an error.
"""

import datetime

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from common.db.locks import advisory_lock

RUN_LOCK = "mail_intake_run"


class Command(BaseCommand):
    help = "Fetch new support email and convert it into tickets."

    def add_arguments(self, parser):
        parser.add_argument(
            "--limit", type=int, default=None,
            help="Maximum messages to process this run.",
        )
        parser.add_argument(
            "--since", default=None,
            help="Fetch mail since YYYY-MM-DD instead of only unseen. "
                 "The recovery flag: safe to repeat, because de-duplication holds.",
        )
        parser.add_argument(
            "--dry-run", action="store_true",
            help="Fetch and parse but write nothing and send nothing.",
        )
        parser.add_argument("--folder", default=None, help="Override the mailbox folder.")
        parser.add_argument(
            "--retry-only", action="store_true",
            help="Re-drive messages left mid-pipeline without contacting the mailbox.",
        )
        parser.add_argument(
            "--no-lock", action="store_true",
            help="Skip the advisory run lock. Escape hatch only.",
        )

    def handle(self, *args, **options):
        from apps.mail_intake.services.intake_orchestrator import retry_pending, run_intake

        if not getattr(settings, "MAIL_INTAKE_ENABLED", False):
            self.stdout.write(
                self.style.WARNING("MAIL_INTAKE_ENABLED is false; nothing to do.")
            )
            return

        since = self._parse_since(options.get("since"))
        limit = options.get("limit") or getattr(settings, "MAIL_INTAKE_BATCH_LIMIT", 50)

        if options.get("no_lock"):
            self._run(run_intake, retry_pending, options, since, limit)
            return

        with advisory_lock(RUN_LOCK) as acquired:
            if not acquired:
                # Not an error: the other run is doing exactly this work.
                self.stdout.write(
                    self.style.WARNING("Another intake run holds the lock; exiting.")
                )
                return
            self._run(run_intake, retry_pending, options, since, limit)

    def _run(self, run_intake, retry_pending, options, since, limit):
        if options.get("retry_only"):
            result = retry_pending(limit=limit)
            self.stdout.write(self.style.SUCCESS(f"Retry sweep: {result.as_dict()}"))
            return

        try:
            result = run_intake(
                limit=limit,
                since=since,
                dry_run=options.get("dry_run", False),
                folder=options.get("folder"),
            )
        except Exception as exc:
            # The message, never a traceback: spec 44 forbids exposing those,
            # and a mailbox password must never reach a log line.
            raise CommandError(f"Mail intake failed: {exc}") from exc

        prefix = "DRY RUN " if options.get("dry_run") else ""
        self.stdout.write(
            self.style.SUCCESS(
                f"{prefix}fetched={result.fetched} registered={result.registered} "
                f"tickets={result.tickets_created} threads={result.thread_updates} "
                f"duplicates={result.duplicates} rejected={result.rejected} "
                f"skipped={result.skipped} empty={result.empty_skipped} "
                f"promotional={result.promotional_skipped} "
                f"no_reply={result.no_reply_skipped} failed={result.failed}"
            )
        )

    def _parse_since(self, value):
        if not value:
            return None
        try:
            return datetime.datetime.strptime(value, "%Y-%m-%d").date()
        except ValueError:
            raise CommandError(f"--since must be YYYY-MM-DD, got {value!r}")
