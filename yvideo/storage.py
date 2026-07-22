from django.contrib.staticfiles.storage import (
    ManifestStaticFilesStorage as DjangoManifestStaticFilesStorage,
)


class ManifestStaticFilesStorage(DjangoManifestStaticFilesStorage):
    """Fingerprint static files, including dependencies imported by ES modules."""

    support_js_module_import_aggregation = True
