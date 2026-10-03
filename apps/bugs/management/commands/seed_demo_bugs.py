"""Generate a realistic spread of demo bugs (development only).

Exists so the dashboard, charts, aging bands and overdue logic can be seen
working against data that resembles real usage, rather than an empty database
or three hand-made rows.
"""

import datetime
import random

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction

from apps.bugs.constants import BugStatus, Environment, TestResult
from apps.bugs.models import Bug
from apps.bugs.services import (
    add_update, assign_bug, change_status, close_bug, create_bug,
    record_test, reopen_bug, resolve_bug,
)
from apps.masters.models import (
    DepartmentMaster, ModuleMaster, PriorityMaster, ProjectMaster,
    RootCauseTypeMaster, SeverityMaster, SiteMaster, SubmoduleMaster,
)
from common.utils.dates import local_today

TITLES = [
    ("Customer approval page returns 500", "The approval screen throws a server error on submit."),
    ("Invoice PDF missing tax breakup", "Generated invoices omit the GST breakup table."),
    ("Stock transfer allows negative quantity", "No validation on the quantity field."),
    ("Payment voucher date defaults to 1970", "Date picker initialises incorrectly."),
    ("Sales order search times out", "Searching by customer name takes over 30 seconds."),
    ("Attendance report shows duplicate rows", "Employees appear twice for split shifts."),
    ("Goods receipt cannot attach documents", "Upload button does nothing."),
    ("Ledger balance mismatch after posting", "Opening balance is not carried forward."),
    ("Job card print layout is cut off", "Right margin overflows on A4."),
    ("Quotation discount not applied", "Line-level discount is ignored in the total."),
    ("User cannot reset their password", "Reset link expires immediately."),
    ("Dashboard chart shows stale figures", "Counts do not refresh after closing a bug."),
    ("Export to Excel drops the last row", "Off-by-one in the export range."),
    ("Mobile menu does not close on navigate", "Drawer stays open after selecting an item."),
    ("Duplicate customer codes allowed", "Unique validation missing on the master."),
    ("Payroll rounding differs from spec", "Half-up rounding applied instead of half-even."),
    ("Session expires during long forms", "Users lose work after 30 minutes."),
    ("Report filter resets on back navigation", "Filter state is not kept in the URL."),
]


class Command(BaseCommand):
    help = "Create a realistic spread of demo bugs across the workflow."

    def add_arguments(self, parser):
        parser.add_argument("--count", type=int, default=40)
        parser.add_argument("--force", action="store_true")

    @transaction.atomic
    def handle(self, *args, **options):
        if not settings.DEBUG and not options["force"]:
            self.stderr.write(self.style.ERROR("Refusing to seed demo bugs with DEBUG=False."))
            return

        User = get_user_model()
        users = {u.username: u for u in User.objects.all()}
        required = ["lead", "kiran", "arun", "qa", "finance"]
        if any(name not in users for name in required):
            self.stderr.write(self.style.ERROR("Run seed_demo_users first."))
            return

        project = ProjectMaster.objects.filter(code="ZERP").first()
        modules = list(ModuleMaster.objects.filter(project=project))
        priorities = {p.code: p for p in PriorityMaster.objects.all()}
        severities = {s.code: s for s in SeverityMaster.objects.all()}
        root_causes = list(RootCauseTypeMaster.objects.all())
        departments = list(DepartmentMaster.objects.all())
        sites = list(SiteMaster.objects.all())

        if not (project and modules and priorities and severities):
            self.stderr.write(self.style.ERROR("Run seed_masters and seed_demo_users first."))
            return

        random.seed(20260914)
        today = local_today()
        devs = [users["kiran"], users["arun"]]
        lead, qa, reporter = users["lead"], users["qa"], users["finance"]

        created = 0
        for index in range(options["count"]):
            title, description = TITLES[index % len(TITLES)]
            module = random.choice(modules)
            submodule = SubmoduleMaster.objects.filter(module=module).order_by("?").first()
            priority_code = random.choices(
                ["CRITICAL", "HIGH", "MEDIUM", "LOW"], weights=[1, 3, 4, 2])[0]
            severity_code = random.choices(
                ["BLOCKER", "CRITICAL", "MAJOR", "MINOR", "COSMETIC"],
                weights=[1, 2, 4, 3, 2])[0]

            age = random.randint(0, 40)
            reported = today - datetime.timedelta(days=age)

            bug = create_bug(
                actor=reporter, reported_by=reporter,
                project=project, module=module, submodule=submodule,
                title=f"{title} ({index + 1})", description=description,
                priority=priorities[priority_code], severity=severities[severity_code],
                department=random.choice(departments) if departments else None,
                site=random.choice(sites) if sites else None,
                environment=random.choice(list(Environment.values)),
                reported_date=reported,
            )
            created += 1

            # Walk each bug a random distance along the real workflow, so the
            # dashboard shows a believable mix rather than 40 identical rows.
            stage = random.choices(
                ["new", "assigned", "progress", "testing", "resolved", "closed", "hold", "rejected"],
                weights=[2, 3, 5, 3, 2, 6, 1, 1])[0]

            if stage == "new":
                continue

            if stage == "rejected":
                change_status(bug=bug, to_status=BugStatus.REJECTED, actor=lead,
                              payload={"rejection_reason": "Working as designed."},
                              remarks="Not a defect.")
                continue

            owner = random.choice(devs)
            assign_bug(bug=bug, new_owner=owner, actor=lead, remarks="Please analyse.")
            bug.refresh_from_db()
            if stage == "assigned":
                continue

            if stage == "hold":
                change_status(bug=bug, to_status=BugStatus.ON_HOLD, actor=owner,
                              payload={"hold_reason": "Awaiting clarification from the user."},
                              remarks="On hold pending input.")
                continue

            change_status(bug=bug, to_status=BugStatus.IN_PROGRESS, actor=owner)
            bug.refresh_from_db()

            # Most, but not all, active bugs have a recent update -- that gap is
            # what the Update Pending screen is for.
            if random.random() < 0.6:
                add_update(bug=bug, actor=owner,
                           update_text="Reproduced the issue and traced it to the serializer.",
                           next_action="Apply the fix and retest.")
                bug.refresh_from_db()

            if stage == "progress":
                continue

            bug.resolution = "Corrected the mapping and added a regression test."
            bug.latest_remarks = "Fix complete, ready for QA."
            bug.save(update_fields=["resolution", "latest_remarks", "updated_at"])
            change_status(bug=bug, to_status=BugStatus.TESTING, actor=owner,
                          remarks="Ready for verification.")
            bug.refresh_from_db()
            if stage == "testing":
                continue

            record_test(bug=bug, actor=qa, test_result=TestResult.PASSED,
                        test_remarks="Verified in UAT.")
            bug.refresh_from_db()

            resolve_bug(bug=bug, actor=owner,
                        root_cause="Incorrect field mapping in the serializer.",
                        resolution="Corrected the mapping and added a regression test.",
                        root_cause_type=random.choice(root_causes) if root_causes else None,
                        resolved_date=reported + datetime.timedelta(days=min(age, 2)))
            bug.refresh_from_db()
            if stage == "resolved":
                continue

            close_bug(bug=bug, actor=lead, closure_remarks="Verified in production.",
                      closed_date=reported + datetime.timedelta(days=min(age, 3)))
            bug.refresh_from_db()

            # A few closed bugs come back, which is what reopen_count is for.
            if random.random() < 0.12:
                reopen_bug(bug=bug, actor=lead,
                           reopen_reason="Recurred for one customer after the release.")

        # Back-date some latest_update_date values. Every bug above was created
        # today, and a status change counts as that day's update, so without
        # this every active bug looks up to date and the Update Pending screen
        # has nothing to show. Written directly because BugUpdate rows are
        # immutable by design -- this adjusts only the denormalised field.
        from apps.bugs.constants import DAILY_UPDATE_REQUIRED_STATUSES

        active = list(Bug.objects.filter(status__in=DAILY_UPDATE_REQUIRED_STATUSES))
        random.shuffle(active)
        stale = active[: int(len(active) * 0.45)]
        for offset, bug in enumerate(stale):
            days_back = 1 + (offset % 4)
            Bug.objects.filter(pk=bug.pk).update(
                latest_update_date=today - datetime.timedelta(days=days_back),
            )

        self.stdout.write(self.style.SUCCESS(
            f"Created {created} demo bugs ({len(stale)} left without today's update)."
        ))
