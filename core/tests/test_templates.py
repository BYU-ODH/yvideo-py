from html.parser import HTMLParser
from pathlib import Path
import re

from django.conf import settings
from django.template import engines
from django.template.loader import render_to_string
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


class _VideoTagParser(HTMLParser):
    """Collects the set of attribute names on each <video> start tag."""

    def __init__(self):
        super().__init__()
        self.video_attributes = []

    def handle_starttag(self, tag, attrs):
        if tag == "video":
            self.video_attributes.append({name for name, _value in attrs})


class InlinePlaybackAttributeTests(SimpleTestCase):
    """The <video> attributes that stop iOS taking playback away from our annotation overlay.

    Without `playsinline`, iOS hands playback to the OS fullscreen player, where
    `#annotation-box` is not composited - so blur annotations silently do not apply. Blurs cover
    copyrighted, violent, and explicit content, and this failure mode is invisible: no error, no
    layout glitch, just no blur. `disableRemotePlayback` closes the same hole for AirPlay/Cast.

    What this test proves is that the attributes are *present*, which is all that can be checked
    from here. It cannot prove the iOS behaviour: Playwright's WebKit is built for the host OS,
    and delegating playback to the OS player is an iOS platform media-stack integration rather
    than a WebKit engine feature, so a browser test would pass identically with these attributes
    deleted. The behaviour itself is verified by hand - see MANUAL_TESTING.md. What this does
    catch is the realistic regression: someone dropping an attribute while editing the tag for
    an unrelated reason.

    Asserting on the partial rather than a rendered page covers all three player surfaces at
    once, since player.html, content_info.html and video_editor.html all include it.
    """

    def test_the_video_element_opts_out_of_native_fullscreen_and_remote_playback(self):
        parser = _VideoTagParser()
        parser.feed(render_to_string("core/partials/player-wrapper.html", {}))

        self.assertEqual(
            len(parser.video_attributes), 1, "expected exactly one <video> element"
        )
        # Compared as parsed attribute names rather than substrings, because "playsinline" is a
        # substring of "webkit-playsinline" - a substring search would still pass with the bare
        # (and load-bearing; the webkit- spelling is the legacy alias) attribute removed. Names
        # are lowercased by HTMLParser, hence "disableremoteplayback".
        for required in ("playsinline", "webkit-playsinline", "disableremoteplayback"):
            with self.subTest(attribute=required):
                self.assertIn(
                    required,
                    parser.video_attributes[0],
                    f"the <video> element lost {required}; see MANUAL_TESTING.md",
                )
