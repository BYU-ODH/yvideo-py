from django.conf import settings


def feature_flags(request):
    return {
        "legacy_migration_enabled": settings.LEGACY_MIGRATION_ENABLED,
    }
