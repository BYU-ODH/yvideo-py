from django.apps import AppConfig


class IcConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "ic"
    verbose_name = "International Cinema"

    def ready(self):
        from django.contrib.auth.models import Group
        from django.db.models.signals import post_migrate

        from core.models import IC_EDITOR_GROUP_NAME

        def create_ic_editor_group(sender, **kwargs):
            Group.objects.get_or_create(name=IC_EDITOR_GROUP_NAME)

        post_migrate.connect(create_ic_editor_group, sender=self, weak=False)
