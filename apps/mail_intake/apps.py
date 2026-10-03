from django.apps import AppConfig


class MailIntakeConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.mail_intake"
    label = "mail_intake"
    verbose_name = "Mail Intake"
