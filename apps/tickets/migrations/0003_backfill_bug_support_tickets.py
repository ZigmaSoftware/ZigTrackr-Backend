from django.db import migrations


def period_for(value):
    return value.strftime("%y%m")


def next_ticket_no(TicketNumberSequence, date_value):
    period = period_for(date_value)
    sequence, _ = TicketNumberSequence.objects.get_or_create(
        period=period, defaults={"last_number": 0}
    )
    sequence.last_number += 1
    sequence.save(update_fields=["last_number", "updated_at"])
    return f"TKT-{period}-{sequence.last_number:04d}"


def ticket_status_for_bug(bug):
    if bug.status == "CLOSED":
        return "CLOSED"
    if bug.status == "REJECTED":
        return "REJECTED"
    if bug.status == "IN_PROGRESS":
        return "IN_PROGRESS"
    if bug.owner_id:
        return "ASSIGNED"
    return "NEW"


def forwards(apps, schema_editor):
    Bug = apps.get_model("bugs", "Bug")
    SupportTicket = apps.get_model("tickets", "SupportTicket")
    TicketNumberSequence = apps.get_model("tickets", "TicketNumberSequence")

    for bug in Bug.objects.filter(is_deleted=False).order_by("id"):
        if SupportTicket.objects.filter(bug_id=bug.id).exists():
            continue
        date_value = bug.reported_date or bug.created_at.date()
        ticket = SupportTicket.objects.create(
            ticket_no=next_ticket_no(TicketNumberSequence, date_value),
            source="MANUAL",
            ticket_type="BUG",
            classification_method="MANUAL",
            classification_status="HUMAN_CONFIRMED",
            classification_reason="Backfilled from existing bug.",
            needs_review=False,
            project_id=bug.project_id,
            module_id=bug.module_id,
            submodule_id=bug.submodule_id,
            title=bug.title,
            description=bug.description,
            reported_by_id=bug.reported_by_id,
            reported_by_email=getattr(bug.reported_by, "email", "") or "",
            reported_by_name=getattr(bug.reported_by, "full_name", "") or getattr(bug.reported_by, "username", ""),
            owner_id=bug.owner_id,
            priority_id=bug.priority_id,
            status=ticket_status_for_bug(bug),
            expected_closure_date=bug.expected_closure_date,
            bug_id=bug.id,
            confirmed_by_id=bug.assigned_by_id or bug.reported_by_id,
            confirmed_at=bug.assigned_date or bug.created_at,
            created_by=bug.created_by,
            updated_by=bug.updated_by,
        )
        SupportTicket.objects.filter(pk=ticket.pk).update(
            created_at=bug.created_at,
            updated_at=bug.updated_at,
        )


def backwards(apps, schema_editor):
    SupportTicket = apps.get_model("tickets", "SupportTicket")
    SupportTicket.objects.filter(
        ticket_type="BUG",
        source="MANUAL",
        classification_reason="Backfilled from existing bug.",
    ).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("tickets", "0002_alter_supportticket_status"),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
