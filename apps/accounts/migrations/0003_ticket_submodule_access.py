"""Preserve existing page visibility without expanding action authority."""

from django.db import migrations

# Frozen values: do not import the live catalog into a historical migration.
SCREENS = (
    ("create", "Create Ticket", "ticket_creation", "tickets.ticket.create"),
    ("unassigned", "Unassigned Tickets", "ticket_creation", "tickets.ticket.view"),
    ("reassign", "Reassign Tickets", "ticket_creation", "tickets.ticket.reassign"),
    ("all", "All Tickets", "ticket_management", "tickets.ticket.view"),
    ("bugs", "Bug Requests", "ticket_management", "tickets.ticket.view"),
    ("services", "Service Requests", "ticket_management", "tickets.ticket.view"),
    ("access", "Access Requests", "ticket_management", "tickets.ticket.view"),
    ("critical", "Critical Tickets", "ticket_management", "tickets.ticket.view"),
    ("overdue", "Overdue Tickets", "ticket_management", "tickets.ticket.view"),
    ("testing", "Testing / Verification", "ticket_management", "tickets.ticket.view"),
    ("closed", "Closed Tickets", "ticket_management", "tickets.ticket.view"),
)


def forwards(apps, schema_editor):
    Permission = apps.get_model("accounts", "Permission")
    RolePermission = apps.get_model("accounts", "RolePermission")
    db = schema_editor.connection.alias
    permissions, grants = Permission.objects.using(db), RolePermission.objects.using(db)
    # Fresh databases are populated by seed_permissions / seed_roles instead.
    if not permissions.filter(codename__startswith="tickets.ticket.").exists():
        return
    for order, (screen, name, module, legacy) in enumerate(SCREENS, start=1):
        permission, created = permissions.get_or_create(codename=f"tickets.{screen}.access", defaults={
            "name": f"Access {name}", "module": module, "screen_code": screen,
            "screen_name": name, "action": "use", "sort_order": 1000 + order * 10,
        })
        if created:
            role_ids = grants.filter(permission__codename=legacy, permission__is_active=True,
                                     permission__is_deleted=False).values_list("role_id", flat=True)
            grants.bulk_create([RolePermission(role_id=role_id, permission_id=permission.pk) for role_id in role_ids])


def backwards(apps, schema_editor):
    Permission = apps.get_model("accounts", "Permission")
    Permission.objects.using(schema_editor.connection.alias).filter(
        codename__in=[f"tickets.{screen}.access" for screen, _, _, _ in SCREENS],
    ).delete()


class Migration(migrations.Migration):
    dependencies = [("accounts", "0002_initial")]
    operations = [migrations.RunPython(forwards, backwards)]
