from pathlib import Path
import re
from tempfile import TemporaryDirectory

from django.contrib.staticfiles.storage import ManifestStaticFilesStorage
from django.core.files.storage import storages
from django.core.management import call_command
from django.test import SimpleTestCase
from django.test import override_settings


class ManifestStaticFilesStorageTests(SimpleTestCase):
    @override_settings(DEBUG=False)
    def test_collectstatic_fingerprints_static_asset_dependencies(self):
        with TemporaryDirectory() as static_root:
            with override_settings(STATIC_ROOT=static_root):
                call_command("collectstatic", interactive=False, verbosity=0)
                storage = storages["staticfiles"]

                self.assertIsInstance(storage, ManifestStaticFilesStorage)
                self.assertTrue(Path(static_root, "staticfiles.json").is_file())
                self.assertRegex(
                    storage.url("css/style.css"),
                    r"/static/css/style\.[0-9a-f]{12}\.css$",
                )
                self.assertRegex(
                    storage.url("js/editor.js"),
                    r"/static/js/editor\.[0-9a-f]{12}\.js$",
                )

                collected_css = Path(
                    static_root, storage.stored_name("css/style.css")
                ).read_text(encoding="utf-8")
                self.assertRegex(
                    collected_css,
                    r"\.\./img/plus\.[0-9a-f]{12}\.svg",
                )

                collected_js = Path(
                    static_root, storage.stored_name("js/editor.js")
                ).read_text(encoding="utf-8")
                self.assertRegex(
                    collected_js,
                    re.compile(r'from "\./utils\.[0-9a-f]{12}\.js";'),
                )
