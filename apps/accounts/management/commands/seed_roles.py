"""Seed roles and their permission grants."""

from django.core.management.base import BaseCommand
from django.db import transaction

from apps.accounts.models import Permission, Role, RolePermission
from common.permissions.codenames import ROLE_DEFINITIONS, ROLE_PERMISSIONS


class Command(BaseCommand):
    help = "Seed system roles and their permission assignments."

    @transaction.atomic
    def handle(self, *args, **options):
        permissions = {p.codename: p for p in Permission.objects.all()}
        if not permissions:
            self.stderr.write(self.style.ERROR(
                "No permissions found. Run seed_permissions first."
            ))
            return

        for code, name, rank, description in ROLE_DEFINITIONS:
            role, created = Role.objects.update_or_create(
                code=code,
                defaults={
                    "name": name,
                    "rank": rank,
                    "description": description,
                    "is_system": True,
                    "is_active": True,
                    "is_deleted": False,
                },
            )

            wanted = set(ROLE_PERMISSIONS.get(code, []))
            existing = set(
                RolePermission.objects.filter(role=role)
                .values_list("permission__codename", flat=True)
            )

            to_add = wanted - existing
            RolePermission.objects.bulk_create([
                RolePermission(role=role, permission=permissions[c])
                for c in to_add if c in permissions
            ])

            # Revoke grants no longer in the catalog, so the seed is
            # authoritative rather than merely additive.
            to_remove = existing - wanted
            if to_remove:
                RolePermission.objects.filter(
                    role=role, permission__codename__in=to_remove
                ).delete()

            verb = "created" if created else "updated"
            self.stdout.write(
                f"  {name:16s} {verb}: +{len(to_add)} -{len(to_remove)} "
                f"({len(wanted)} total)"
            )

        self.stdout.write(self.style.SUCCESS("Roles seeded."))
