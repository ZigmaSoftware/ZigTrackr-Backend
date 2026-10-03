from django.db import migrations


def preserve_original_mail_links(apps, schema_editor):
    Ticket = apps.get_model("tickets", "SupportTicket")
    Mail = apps.get_model("mail_intake", "MailIntake")
    for ticket in Ticket.objects.using(schema_editor.connection.alias).exclude(
        mail_intake_id__isnull=True
    ).iterator():
        mail = Mail.objects.using(schema_editor.connection.alias).get(pk=ticket.mail_intake_id)
        if mail.linked_ticket_id not in (None, ticket.pk):
            raise RuntimeError(f"Mail {mail.pk} is linked to another ticket.")
        if Mail.objects.using(schema_editor.connection.alias).filter(
            linked_ticket_id=ticket.pk, is_thread_reply=False
        ).exclude(pk=mail.pk).exists():
            raise RuntimeError(f"Ticket {ticket.pk} has more than one original mail.")
        Mail.objects.using(schema_editor.connection.alias).filter(pk=mail.pk).update(
            linked_ticket_id=ticket.pk, is_thread_reply=False
        )


class Migration(migrations.Migration):
    dependencies = [
        ("tickets", "0011_ticketchatmessage_delivered_at_and_more"),
        ("mail_intake", "0003_alter_mailintake_last_error_code_and_more"),
    ]

    operations = [
        migrations.RunPython(preserve_original_mail_links, migrations.RunPython.noop),
        migrations.RemoveIndex(model_name="supportticket", name="idx_ticket_mail_intake"),
        migrations.RemoveField(model_name="supportticket", name="mail_intake"),
    ]
