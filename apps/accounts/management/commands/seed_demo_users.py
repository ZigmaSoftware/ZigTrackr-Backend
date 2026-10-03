"""Seed one demo user per role, plus a sample project hierarchy."""

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction

from apps.accounts.models import Role, UserRole
from apps.masters.models import (
    DepartmentMaster,
    ModuleMaster,
    ProjectMaster,
    SiteMaster,
    SubmoduleMaster,
    TeamMaster,
)
from common.permissions.codenames import (
    ROLE_ADMIN,
    ROLE_DEVELOPER,
    ROLE_MANAGEMENT,
    ROLE_REPORTER,
    ROLE_TEAM_LEAD,
    ROLE_TESTER,
)

DEFAULT_PASSWORD = "Zigma@12345"

# username, full name, email, role code, employee code
DEMO_USERS = [
    ("admin", "System Admin", "admin@zigma.in", ROLE_ADMIN, "EMP001"),
    ("lead", "Priya Raman", "lead@zigma.in", ROLE_TEAM_LEAD, "EMP002"),
    ("kiran", "Kiran Kumar", "kiran@zigma.in", ROLE_DEVELOPER, "EMP003"),
    ("arun", "Arun Prakash", "arun@zigma.in", ROLE_DEVELOPER, "EMP004"),
    ("qa", "Divya Menon", "qa@zigma.in", ROLE_TESTER, "EMP005"),
    ("finance", "Finance User", "finance@zigma.in", ROLE_REPORTER, "EMP006"),
    ("manager", "Ravi Shankar", "manager@zigma.in", ROLE_MANAGEMENT, "EMP007"),
]

SAMPLE_MODULES = {
    "Sales": ["Customer Creation", "Quotation", "Sales Order", "Invoice"],
    "Stores": ["Goods Receipt", "Issue Note", "Stock Transfer"],
    "Accounts": ["Payment Voucher", "Receipt", "Ledger"],
    "Production": ["Work Order", "Job Card"],
    "HR": ["Attendance", "Payroll"],
}


class Command(BaseCommand):
    help = "Seed demo users, a team, and a sample project hierarchy (development only)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--force", action="store_true",
            help="Allow seeding when DEBUG is False.",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        if not settings.DEBUG and not options["force"]:
            self.stderr.write(self.style.ERROR(
                "Refusing to seed demo users with DEBUG=False. Pass --force if you "
                "really intend to create known-password accounts here."
            ))
            return

        User = get_user_model()
        roles = {r.code: r for r in Role.objects.all()}
        if not roles:
            self.stderr.write(self.style.ERROR("No roles found. Run seed_roles first."))
            return

        it_dept = DepartmentMaster.objects.filter(name="IT").first()
        ho_site = SiteMaster.objects.filter(name="HO").first()
        team, _ = TeamMaster.objects.get_or_create(
            name="ERP Core Team", defaults={"code": "ERP", "is_active": True},
        )

        created_users = []
        for username, full_name, email, role_code, emp_code in DEMO_USERS:
            user, created = User.objects.get_or_create(
                username=username,
                defaults={
                    "email": email,
                    "full_name": full_name,
                    "employee_code": emp_code,
                    "department": it_dept,
                    "site": ho_site,
                    "team": team,
                    "is_active": True,
                    "is_staff": role_code == ROLE_ADMIN,
                    "is_superuser": role_code == ROLE_ADMIN,
                },
            )
            if created:
                user.set_password(DEFAULT_PASSWORD)
                user.save()
                created_users.append(username)

            role = roles.get(role_code)
            if role:
                UserRole.objects.get_or_create(user=user, role=role, defaults={"is_active": True})

        lead = User.objects.filter(username="lead").first()
        if lead and not team.team_lead_id:
            team.team_lead = lead
            team.save(update_fields=["team_lead", "updated_at"])

        project, _ = ProjectMaster.objects.get_or_create(
            code="ZERP",
            defaults={"name": "Zigma ERP", "project_lead": lead, "is_active": True},
        )
        module_count = submodule_count = 0
        for module_name, submodules in SAMPLE_MODULES.items():
            module, _ = ModuleMaster.objects.get_or_create(
                project=project, name=module_name, defaults={"is_active": True},
            )
            module_count += 1
            for sub_name in submodules:
                SubmoduleMaster.objects.get_or_create(
                    module=module, name=sub_name, defaults={"is_active": True},
                )
                submodule_count += 1

        self.stdout.write(f"  Users created:   {len(created_users)} ({', '.join(created_users) or 'none new'})")
        self.stdout.write(f"  Project/modules: {project.name} / {module_count} modules, {submodule_count} submodules")
        self.stdout.write(self.style.SUCCESS(
            f"Demo data seeded. Password for all demo users: {DEFAULT_PASSWORD}"
        ))
