from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [("tickets", "0012_remove_supportticket_mail_intake")]

    operations = [
        migrations.CreateModel(
            name="OutboundMail",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("event_key", models.CharField(max_length=160, unique=True)),
                ("kind", models.CharField(max_length=16)),
                ("from_email", models.EmailField(max_length=320)),
                ("recipient", models.EmailField(max_length=320)),
                ("subject", models.CharField(max_length=255)),
                ("body", models.TextField()),
                ("message_id", models.CharField(max_length=255)),
                ("in_reply_to", models.CharField(blank=True, default="", max_length=255)),
                ("status", models.CharField(choices=[("PENDING", "Pending"), ("SENDING", "Sending"), ("RETRY", "Retry"), ("SENT", "Sent"), ("FAILED", "Failed")], default="PENDING", max_length=10)),
                ("attempts", models.PositiveSmallIntegerField(default=0)),
                ("next_attempt_at", models.DateTimeField(blank=True, null=True)),
                ("claimed_at", models.DateTimeField(blank=True, null=True)),
                ("sent_at", models.DateTimeField(blank=True, null=True)),
                ("last_error", models.CharField(blank=True, default="", max_length=500)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("ticket", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="outbound_mail", to="tickets.supportticket")),
            ],
            options={"db_table": "ticket_outbound_mail", "indexes": [models.Index(fields=["status", "next_attempt_at"], name="idx_outmail_due")]},
        ),
    ]
