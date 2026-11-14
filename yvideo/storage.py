import os

from django.conf import settings
from django.contrib.staticfiles.storage import (
    ManifestStaticFilesStorage as DjangoManifestStaticFilesStorage,
)
from django.core.files.storage import FileSystemStorage
from django.utils._os import safe_join


class ManifestStaticFilesStorage(DjangoManifestStaticFilesStorage):
    """Fingerprint static files, including dependencies imported by ES modules."""

    support_js_module_import_aggregation = True


class ResourceFileStorage(FileSystemStorage):
    """MEDIA_ROOT storage that keeps International Cinema projection masters elsewhere.

    Names starting with core.models.PROJECTION_FILE_PREFIX resolve under
    settings.IC_PROJECTION_ROOT. Every FileSystemStorage operation goes through
    path(), so open/save/delete/exists/size all follow the same routing. Masters are
    full-length, full-resolution films and must not sit in MEDIA_ROOT, which the web
    server publishes directly (#531).
    """

    def path(self, name):
        from core.models import PROJECTION_FILE_PREFIX

        if name.startswith(PROJECTION_FILE_PREFIX):
            return safe_join(
                os.fspath(settings.IC_PROJECTION_ROOT),
                name[len(PROJECTION_FILE_PREFIX) :],
            )
        return super().path(name)
