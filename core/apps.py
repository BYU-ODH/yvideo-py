from django.apps import AppConfig


class CoreConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "core"

    def ready(self):
        from django.contrib.auth.models import Group
        from django.db.models.signals import post_migrate

        from .language_data import seed_languages
        from .models import LAB_ASSISTANT_GROUP_NAME
        from .models import Language

        def create_lab_assistant_group(sender, **kwargs):
            Group.objects.get_or_create(name=LAB_ASSISTANT_GROUP_NAME)

        def create_default_languages(sender, **kwargs):
            seed_languages(Language)

        post_migrate.connect(create_lab_assistant_group, sender=self, weak=False)
        post_migrate.connect(create_default_languages, sender=self, weak=False)
