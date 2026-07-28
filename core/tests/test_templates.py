from pathlib import Path
import re

from django.conf import settings
from django.template import engines
from django.test import SimpleTestCase

TEMPLATE_REFERENCE_PATTERN = re.compile(
    r"""{%\s*(?:extends|include)\s+["'](?P<name>[^"']+)["']"""
)


class TemplateValidationTests(SimpleTestCase):
    def assert_templates_load_and_resolve_static_references(
        self, template_root, template_prefix=None
    ):
        template_engine = engines["django"].engine
        template_paths = sorted(template_root.rglob("*.html"))

        self.assertTrue(template_paths, f"Expected templates under {template_root}.")

        referenced_template_names = set()
        for template_path in template_paths:
            relative_path = template_path.relative_to(template_root)
            template_name = str(
                Path(template_prefix) / relative_path
                if template_prefix
                else relative_path
            )
            with self.subTest(template=template_name):
                template = template_engine.get_template(template_name)
                self.assertEqual(
                    Path(template.origin.name).resolve(), template_path.resolve()
                )

            source = template_path.read_text(encoding="utf-8")
            referenced_template_names.update(
                match.group("name")
                for match in TEMPLATE_REFERENCE_PATTERN.finditer(source)
            )

        for referenced_template_name in sorted(referenced_template_names):
            with self.subTest(reference=referenced_template_name):
                template_engine.get_template(referenced_template_name)

    def test_all_core_templates_load_and_resolve_static_references(self):
        template_root = Path(settings.BASE_DIR) / "core" / "templates" / "core"
        self.assert_templates_load_and_resolve_static_references(
            template_root, template_prefix="core"
        )

    def test_all_project_templates_load_and_resolve_static_references(self):
        template_root = Path(settings.BASE_DIR) / "templates"
        self.assert_templates_load_and_resolve_static_references(template_root)
