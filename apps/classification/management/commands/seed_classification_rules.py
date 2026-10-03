"""Seed the system classification and module-mapping rules (spec 16, 20).

Re-runnable. Crucially it does NOT reset score or is_active on a rule that
already exists: those are the tuning knobs spec 16 gives Admins, and a seed that
overwrote them would silently undo an operator's work on every deploy.
"""

from django.core.management.base import BaseCommand
from django.db import transaction

from apps.classification.models import ClassificationRule, ModuleMappingRule
from apps.classification.services.normalize import normalize_text

# ticket_type, rule_type, pattern, score  -- spec 16's table, verbatim.
CLASSIFICATION_RULES = [
    ("BUG", "SUBJECT_PREFIX", "[BUG]", 100),
    ("SERVICE_REQUEST", "SUBJECT_PREFIX", "[SERVICE]", 100),
    ("ACCESS_REQUEST", "SUBJECT_PREFIX", "[ACCESS]", 100),
    ("BUG", "EXACT_PHRASE", "500 error", 100),
    ("BUG", "EXACT_PHRASE", "not working", 85),
    ("BUG", "EXACT_PHRASE", "unable to", 65),
    ("BUG", "KEYWORD", "error", 80),
    ("BUG", "KEYWORD", "bug", 90),
    ("BUG", "KEYWORD", "failed", 75),
    ("ACCESS_REQUEST", "EXACT_PHRASE", "provide access", 100),
    ("ACCESS_REQUEST", "KEYWORD", "permission", 90),
    ("ACCESS_REQUEST", "KEYWORD", "access", 70),
    ("SERVICE_REQUEST", "EXACT_PHRASE", "reset password", 100),
    ("SERVICE_REQUEST", "EXACT_PHRASE", "install software", 100),
    ("SERVICE_REQUEST", "EXACT_PHRASE", "create login", 90),
]

# Fuzzy variants of the words spec 17 names in its typo examples. Kept separate
# so the provenance of each block is obvious.
FUZZY_RULES = [
    ("BUG", "FUZZY_KEYWORD", "error", 80),
    ("BUG", "FUZZY_KEYWORD", "not working", 85),
    ("ACCESS_REQUEST", "FUZZY_KEYWORD", "permission", 90),
    ("SERVICE_REQUEST", "FUZZY_KEYWORD", "reset password", 90),
]

# phrase, project code, module name, submodule name -- spec 20's example table.
# Seeded only where the referenced masters actually exist.
MODULE_MAPPINGS = [
    ("user creation", "User Management", "User Creation"),
    ("customer creation", "Sales", "Customer Creation"),
    ("invoice", "Sales", "Invoice Generation"),
    ("grn", "Stores", "GRN"),
    ("purchase indent", "Purchase", "Indent"),
    ("payable", "Accounts", "Payable"),
    ("payroll", "Payroll", None),
]


class Command(BaseCommand):
    help = "Seed system classification rules and module mapping rules."

    def add_arguments(self, parser):
        parser.add_argument(
            "--project",
            default=None,
            help="Project code or name to attach module mappings to. "
                 "Without it, only classification rules are seeded.",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        created, skipped = self._seed_rules()
        self.stdout.write(
            self.style.SUCCESS(
                f"Classification rules: {created} created, {skipped} already present."
            )
        )

        project_ref = options.get("project")
        if not project_ref:
            self.stdout.write(
                "No --project given; skipping module mapping rules. "
                "Re-run with --project <code> once projects are seeded."
            )
            return

        mapped, missing = self._seed_mappings(project_ref)
        self.stdout.write(
            self.style.SUCCESS(f"Module mapping rules: {mapped} created.")
        )
        for name in missing:
            self.stdout.write(self.style.WARNING(f"  skipped (no such module): {name}"))

    def _seed_rules(self):
        created = skipped = 0
        for ticket_type, rule_type, pattern, score in CLASSIFICATION_RULES + FUZZY_RULES:
            lookup = {
                "ticket_type": ticket_type,
                "rule_type": rule_type,
                "normalized_pattern": normalize_text(pattern),
                "is_deleted": False,
            }
            if ClassificationRule.objects.filter(**lookup).exists():
                # Present already: leave score and is_active exactly as the
                # operator tuned them.
                skipped += 1
                continue
            ClassificationRule.objects.create(
                ticket_type=ticket_type,
                rule_type=rule_type,
                pattern=pattern,
                score=score,
                is_system=True,
            )
            created += 1
        return created, skipped

    def _seed_mappings(self, project_ref):
        from apps.masters.models import ModuleMaster, ProjectMaster, SubmoduleMaster

        project = (
            ProjectMaster.objects.filter(code__iexact=project_ref, is_deleted=False).first()
            or ProjectMaster.objects.filter(name__iexact=project_ref, is_deleted=False).first()
        )
        if project is None:
            self.stderr.write(self.style.ERROR(f"No project matching '{project_ref}'."))
            return 0, []

        mapped = 0
        missing = []
        for phrase, module_name, submodule_name in MODULE_MAPPINGS:
            module = ModuleMaster.objects.filter(
                project=project, name__iexact=module_name, is_deleted=False
            ).first()
            if module is None:
                missing.append(f"{phrase} -> {module_name}")
                continue

            submodule = None
            if submodule_name:
                submodule = SubmoduleMaster.objects.filter(
                    module=module, name__iexact=submodule_name, is_deleted=False
                ).first()

            normalized = normalize_text(phrase)
            if ModuleMappingRule.objects.filter(
                normalized_phrase=normalized, project=project, is_deleted=False
            ).exists():
                continue

            ModuleMappingRule.objects.create(
                keyword_or_phrase=phrase,
                project=project,
                module=module,
                submodule=submodule,
                score=100,
                is_system=True,
            )
            mapped += 1
        return mapped, missing
