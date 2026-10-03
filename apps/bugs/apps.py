from django.apps import AppConfig


class BugsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.bugs"
    label = "bugs"
    verbose_name = "Bug Management"
