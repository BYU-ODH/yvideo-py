from django.apps import AppConfig


class CoreConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "core"

    def ready(self):
        from .legacy_dump_scheduler import start_legacy_dump_scheduler

        start_legacy_dump_scheduler()
