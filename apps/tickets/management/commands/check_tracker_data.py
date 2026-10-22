"""Read-only integrity checks, including relationships a single-column FK cannot express."""

from django.apps import apps
from django.core.management.base import BaseCommand, CommandError
from django.db.models import F, Q


class Command(BaseCommand):
    help = "Check company ownership and ticket relationships without modifying any rows."

    def add_arguments(self, parser):
        parser.add_argument("--fail-on-error", action="store_true")

    def handle(self, *args, **options):
        from apps.accounts.models import User
        from apps.tickets.models import SupportTicket
        from apps.bugs.models import Bug
        from apps.mail_intake.models import MailIntake

        problems = 0

        def check(label, queryset):
            nonlocal problems
            count = queryset.count()
            problems += count
            self.stdout.write(f"{label}: {count}")
            if count:
                self.stdout.write("  row IDs: " + ", ".join(map(str, queryset.values_list("pk", flat=True)[:20])))

        check("Login accounts without company", User.objects.filter(company__isnull=True, is_superuser=False, is_active=True).exclude(username="mail.intake"))
        for name in ("ProjectMaster", "DepartmentMaster", "TeamMaster", "SiteMaster", "PriorityMaster", "SeverityMaster", "RootCauseTypeMaster"):
            model = apps.get_model("masters", name)
            check(f"{model._meta.db_table}: missing company", model.objects.filter(company__isnull=True, is_deleted=False))
        for model in (SupportTicket, Bug):
            qs = model.objects.filter(is_deleted=False)
            fields = {"project": "company_id", "module": "project__company_id", "submodule": "module__project__company_id",
                      "priority": "company_id", "severity": "company_id", "department": "company_id", "site": "company_id", "owner": "company_id"}
            for field, parent in fields.items():
                check(f"{model._meta.db_table}: {field} company mismatch", qs.filter(**{f"{field}__isnull": False}).filter(
                    Q(company__isnull=True) | ~Q(**{f"{field}__{parent}": F("company_id")})))
            check(f"{model._meta.db_table}: module outside project", qs.filter(module__isnull=False).filter(Q(project__isnull=True) | ~Q(module__project_id=F("project_id"))))
            check(f"{model._meta.db_table}: submodule outside module", qs.filter(submodule__isnull=False).filter(Q(module__isnull=True) | ~Q(submodule__module_id=F("module_id"))))
        check("Ticket/bug company mismatch", SupportTicket.objects.filter(bug__isnull=False).filter(
            Q(company__isnull=True, bug__company__isnull=False) | Q(company__isnull=False, bug__company__isnull=True) | ~Q(company_id=F("bug__company_id"))))
        check("Mail/ticket company mismatch", MailIntake.objects.filter(linked_ticket__isnull=False).filter(
            Q(company__isnull=True, linked_ticket__company__isnull=False) | Q(company__isnull=False, linked_ticket__company__isnull=True) | ~Q(company_id=F("linked_ticket__company_id"))))
        self.stdout.write(f"Unmapped tickets (Super Admin only): {SupportTicket.objects.filter(company__isnull=True, is_deleted=False).count()}")
        if problems and options["fail_on_error"]:
            raise CommandError(f"Found {problems} integrity issue(s). No data was changed.")
        self.stdout.write(self.style.SUCCESS("Integrity checks complete. No data was changed."))
