from django.apps import AppConfig


class SuperAdminConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.superAdmin"
    verbose_name = "Super administration"
