import os
import subprocess
import sys

from django.conf import settings
from django.test import SimpleTestCase


class AdminAutodiscoveryTests(SimpleTestCase):
    """Admin autodiscovery imports `core.admin` and nothing else, so a model
    registered in another module of this app is missing from the real admin
    unless `core.admin` imports that module. Checked in a subprocess because
    test modules that import those admin modules register their models as an
    import side effect, which hides the problem from every in-process test."""

    def _is_registered_after_setup(self, import_path, model_name):
        script = (
            "import django;"
            "django.setup();"
            "from django.contrib import admin;"
            f"from {import_path} import {model_name};"
            f"print(admin.site.is_registered({model_name}))"
        )
        result = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            check=True,
            cwd=settings.BASE_DIR,
            env={**os.environ, "DJANGO_SETTINGS_MODULE": "yvideo.settings"},
        )
        return result.stdout.strip().splitlines()[-1]

    def test_legacy_migration_request_is_registered(self):
        self.assertEqual(
            self._is_registered_after_setup(
                "core.legacy_migration", "LegacyMigrationRequest"
            ),
            "True",
        )
