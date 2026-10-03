"""Seed classification masters and optional sample organisation data."""

from django.core.management.base import BaseCommand
from django.db import transaction

from apps.masters.models import (
    DepartmentMaster,
    PriorityMaster,
    RootCauseTypeMaster,
    SeverityMaster,
    SiteMaster,
)

# code, name, rank, colour, sla_days  (spec 7)
PRIORITIES = [
    ("CRITICAL", "Critical", 10, "#DC2626", 1),
    ("HIGH", "High", 20, "#EA580C", 3),
    ("MEDIUM", "Medium", 30, "#CA8A04", 7),
    ("LOW", "Low", 40, "#2563EB", 15),
]

# code, name, rank, colour  (spec 8)
SEVERITIES = [
    ("BLOCKER", "Blocker", 10, "#991B1B"),
    ("CRITICAL", "Critical", 20, "#DC2626"),
    ("MAJOR", "Major", 30, "#EA580C"),
    ("MINOR", "Minor", 40, "#CA8A04"),
    ("COSMETIC", "Cosmetic", 50, "#64748B"),
]

# spec 9, all fifteen in order
ROOT_CAUSE_TYPES = [
    ("REQUIREMENT_GAP", "Requirement Gap"),
    ("CODING_ISSUE", "Coding Issue"),
    ("DATABASE_ISSUE", "Database Issue"),
    ("API_ISSUE", "API Issue"),
    ("UI_ISSUE", "UI Issue"),
    ("VALIDATION_MISSING", "Validation Missing"),
    ("CONFIGURATION", "Configuration"),
    ("DATA_ISSUE", "Data Issue"),
    ("DEPLOYMENT_ISSUE", "Deployment Issue"),
    ("THIRD_PARTY_API", "Third-party API"),
    ("INFRASTRUCTURE", "Infrastructure"),
    ("PERFORMANCE_ISSUE", "Performance Issue"),
    ("INTEGRATION_ISSUE", "Integration Issue"),
    ("USER_ERROR", "User Error"),
    ("OTHER", "Other"),
]

DEPARTMENTS = ["Finance", "Sales", "Stores", "Production", "HR", "IT"]
SITES = ["HO", "Plant 1", "Plant 2"]


class Command(BaseCommand):
    help = "Seed priority, severity, root cause type and sample org masters."

    def add_arguments(self, parser):
        parser.add_argument(
            "--skip-org", action="store_true",
            help="Skip sample departments and sites.",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        for code, name, rank, color, sla in PRIORITIES:
            PriorityMaster.objects.update_or_create(
                code=code,
                defaults={"name": name, "rank": rank, "color": color,
                          "sla_days": sla, "is_system": True, "is_active": True},
            )
        self.stdout.write(f"  Priorities:        {len(PRIORITIES)}")

        for code, name, rank, color in SEVERITIES:
            SeverityMaster.objects.update_or_create(
                code=code,
                defaults={"name": name, "rank": rank, "color": color,
                          "is_system": True, "is_active": True},
            )
        self.stdout.write(f"  Severities:        {len(SEVERITIES)}")

        for order, (code, name) in enumerate(ROOT_CAUSE_TYPES, start=1):
            RootCauseTypeMaster.objects.update_or_create(
                code=code,
                defaults={"name": name, "rank": order * 10,
                          "is_system": True, "is_active": True},
            )
        self.stdout.write(f"  Root cause types:  {len(ROOT_CAUSE_TYPES)}")

        if not options["skip_org"]:
            for name in DEPARTMENTS:
                DepartmentMaster.objects.get_or_create(name=name, defaults={"is_active": True})
            for name in SITES:
                SiteMaster.objects.get_or_create(name=name, defaults={"is_active": True})
            self.stdout.write(f"  Departments/Sites: {len(DEPARTMENTS)}/{len(SITES)}")

        self.stdout.write(self.style.SUCCESS("Masters seeded."))
