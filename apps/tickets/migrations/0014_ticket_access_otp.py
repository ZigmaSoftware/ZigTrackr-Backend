import uuid

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [("tickets", "0013_outbound_mail")]

    operations = [
        migrations.AddField(
            model_name="outboundmail",
            name="html_body",
            field=models.TextField(blank=True, default=""),
        ),
        migrations.AddField(
            model_name="outboundmail",
            name="expires_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.CreateModel(
            name="TicketAccessOtp",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("unique_id", models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ("email", models.CharField(max_length=320)),
                ("code_hash", models.CharField(max_length=128)),
                ("expires_at", models.DateTimeField()),
                ("attempts", models.PositiveSmallIntegerField(default=0)),
                ("consumed_at", models.DateTimeField(blank=True, null=True)),
                ("request_ip", models.GenericIPAddressField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("ticket", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="access_otps", to="tickets.supportticket")),
            ],
            options={
                "db_table": "ticket_access_otp",
                "indexes": [
                    models.Index(fields=["ticket", "email", "consumed_at"], name="idx_ticket_otp_lookup"),
                    models.Index(fields=["expires_at"], name="idx_ticket_otp_expiry"),
                ],
            },
        ),
    ]
