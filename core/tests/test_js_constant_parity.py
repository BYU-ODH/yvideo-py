"""The blur constants the browser keeps its own copy of, held to the values in core/models.py.

The rig has to clamp, snap and predict between animation frames, without a round trip, so a handful
of the numbers in core/models.py are re-declared in JavaScript. Two copies of one number drift in
silence: the browser files a drag under a point the server files somewhere else, or clamps a box the
server then clamps differently, and every test still passes.

The *behavioural* mirrors are already held together by shared case tables - geometry_at/rectAtTime
and reconcile_positions/pointsLostByRetiming, in core/tests/test_blur_positions.py and
tests/js/blur-retiming.test.js. This module holds the raw numbers underneath them, which no case
table pins: a table written against 0.05 keeps passing when one side becomes 0.06.

Parsed out of the source rather than asserted in a JS test because Python is where the source of
truth lives, and because `manage.py test core` is the suite that always runs.

Deliberately not listed here: NUDGE_PERCENT, SCRUB_SECONDS, SAME_TIME_SECONDS and the like. Those
are properties of the browser's input handling with no server counterpart, so there is nothing for
them to disagree with.
"""

from pathlib import Path
import re

from django.test import SimpleTestCase

from core.models import BLUR_MIN_HEIGHT
from core.models import BLUR_MIN_WIDTH
from core.models import BLUR_RETIME_TOLERANCE_SECONDS
from core.models import BLUR_SNAP_SECONDS
from core.models import BLUR_TIME_PRECISION

REPO_ROOT = Path(__file__).resolve().parents[2]

# IC_player carries its own copy of video-geometry.js (it ships as a separate Electron bundle and
# cannot import from core/static), so it is a third place these numbers live and is checked too.
GEOMETRY_MODULES = [
    "core/static/js/video-geometry.js",
    "IC_player/src/video-geometry.js",
]

# (js module, js constant, python value it mirrors)
MIRRORED_CONSTANTS = [
    ("core/static/js/BlurEditor.js", "MIN_WIDTH", BLUR_MIN_WIDTH),
    ("core/static/js/BlurEditor.js", "MIN_HEIGHT", BLUR_MIN_HEIGHT),
    ("core/static/js/BlurEditor.js", "SNAP_SECONDS", BLUR_SNAP_SECONDS),
    *[
        (module, name, value)
        for module in GEOMETRY_MODULES
        for name, value in [
            ("TIME_PRECISION", BLUR_TIME_PRECISION),
            ("RETIME_TOLERANCE_SECONDS", BLUR_RETIME_TOLERANCE_SECONDS),
        ]
    ],
]


def read_js_constant(module_path, name):
    """The numeric literal `name` is declared with, or None if it is not declared at all.

    Returning None rather than raising lets the test report a renamed constant as its own failure:
    a rename is the same drift as a changed value, and it must not read as "nothing to check".
    """
    source = (REPO_ROOT / module_path).read_text()
    match = re.search(
        rf"^(?:export\s+)?const\s+{re.escape(name)}\s*=\s*(-?\d+(?:\.\d+)?)\s*;",
        source,
        re.MULTILINE,
    )
    return float(match.group(1)) if match else None


class JsConstantParityTests(SimpleTestCase):
    def test_mirrored_constants_match_the_python_values(self):
        for module_path, name, expected in MIRRORED_CONSTANTS:
            with self.subTest(module=module_path, constant=name):
                found = read_js_constant(module_path, name)
                self.assertIsNotNone(
                    found,
                    f"{name} is no longer declared as a plain numeric const in {module_path}. "
                    f"If it moved or was renamed, update MIRRORED_CONSTANTS; if the browser no "
                    f"longer needs it, drop the entry.",
                )
                self.assertEqual(
                    found,
                    float(expected),
                    f"{module_path} has {name} = {found}, but core/models.py says {expected}. "
                    f"The browser and the server have to agree on this number.",
                )

    def test_the_two_copies_of_video_geometry_agree(self):
        """The duplicated module is a copy, so a fix in one belongs in the other.

        Not a byte comparison of the whole file - that would fail on an unrelated formatting
        difference and teach people to skip the test. The constants above are the part where a
        divergence is silently wrong rather than merely untidy; this covers the exported arithmetic
        those constants feed, by name, so a function added to one copy and not the other is caught.
        """
        exported = [
            set(
                re.findall(
                    r"^export function (\w+)", (REPO_ROOT / module).read_text(), re.M
                )
            )
            for module in GEOMETRY_MODULES
        ]
        self.assertEqual(
            exported[0],
            exported[1],
            f"{GEOMETRY_MODULES[0]} and {GEOMETRY_MODULES[1]} no longer export the same "
            f"functions. They are copies of one module; port the change to both.",
        )
