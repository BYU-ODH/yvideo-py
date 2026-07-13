from pathlib import Path
import re

from django.conf import settings
from django.template import engines
from django.test import SimpleTestCase

TEMPLATE_REFERENCE_PATTERN = re.compile(
    r"""{%\s*(?:extends|include)\s+["'](?P<name>[^"']+)["']"""
)


class TemplateValidationTests(SimpleTestCase):
    def test_all_project_templates_load_and_resolve_static_references(self):
        template_engine = engines["django"].engine
        template_root = Path(settings.BASE_DIR) / "core" / "templates" / "core"
        template_names = sorted(
            str(path.relative_to(template_root))
            for path in template_root.rglob("*.html")
        )

        self.assertTrue(template_names, "Expected at least one project template.")

        referenced_template_names = set()
        for template_name in template_names:
            with self.subTest(template=template_name):
                template_engine.get_template(template_name)

            source = (template_root / template_name).read_text(encoding="utf-8")
            referenced_template_names.update(
                match.group("name")
                for match in TEMPLATE_REFERENCE_PATTERN.finditer(source)
            )

        for referenced_template_name in sorted(referenced_template_names):
            with self.subTest(reference=referenced_template_name):
                template_engine.get_template(referenced_template_name)
