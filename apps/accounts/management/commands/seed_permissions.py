"""Materialise the declarative permission catalog into the database."""

from django.core.management.base import BaseCommand
from django.db import transaction

from apps.accounts.models import Permission
from common.permissions.codenames import PERMISSION_CATALOG


class Command(BaseCommand):
    help = "Seed the permissions table from common.permissions.codenames."

    @transaction.atomic
    def handle(self, *args, **options):
        created = updated = 0
        for order, row in enumerate(PERMISSION_CATALOG, start=1):
            codename, name, module, screen_code, screen_name, action = row
            _, was_created = Permission.objects.update_or_create(
                codename=codename,
                defaults={
                    "name": name,
                    "module": module,
                    "screen_code": screen_code,
                    "screen_name": screen_name,
                    "action": action,
                    "sort_order": order * 10,
                    "is_active": True,
                    "is_deleted": False,
                },
            )
            created += int(was_created)
            updated += int(not was_created)
        self.stdout.write(self.style.SUCCESS(
            f"Permissions seeded: {created} created, {updated} updated."
        ))
