from django.conf import settings

from .permissions import can_request_legacy_migration


def feature_flags(request):
    enabled = settings.LEGACY_MIGRATION_ENABLED
    return {
        "legacy_migration_enabled": enabled,
        # Both halves of the gate in one value: offering the link to someone the
        # endpoint would answer with 404 or 403 is what #379 asked us to stop doing.
        "can_request_legacy_migration": enabled
        and can_request_legacy_migration(request.user),
    }
