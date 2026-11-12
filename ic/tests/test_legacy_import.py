"""Importing the Electron IC Player's JSON, held to the browser's own conversion."""

import csv
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from django.core.management import CommandError
from django.core.management import call_command
from django.test import TestCase

from core.factories import ContentFactory
from core.factories import PlaylistFactory
from core.models import BlankAnnotation
from core.models import BlurAnchorPoint
from core.models import BlurAnnotation
from core.models import MuteAnnotation
from core.models import SkipAnnotation
from ic.legacy import convert_legacy_annotations
from ic.legacy import import_film
from ic.models import ICUpload
from ic.models import MediaJob
from ic.testing import FFMPEG_AVAILABLE
from ic.testing import ICTempDirsMixin
from ic.testing import make_ic_editor
from ic.testing import make_mp4

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURES = [
    REPO_ROOT / "IC_player/resources/example/KungFuPandamv_IC.json",
    REPO_ROOT / "IC_player/resources/kung-test/KungFuPandamv_IC.json",
]

SAMPLE = [
    {
        "options": {
            "label": "Logo",
            "start": "21",
            "end": "35",
            "type": "skip",
            "details": {},
        }
    },
    {
        "options": {
            "label": "Words",
            "start": "37",
            "end": "48",
            "type": "mute",
            "details": {},
        }
    },
    {
        "options": {
            "label": "Bridge",
            "start": "40",
            "end": "45",
            "type": "blank",
            "details": {},
        }
    },
    {
        "options": {
            "label": "Panda",
            "start": "2",
            "end": "10",
            "type": "blur",
            "details": {},
        }
    },
    {
        "options": {
            "label": "Sky",
            "start": "4",
            "end": "6",
            "type": "censor",
            "details": {
                "type": "black",
                "interpolate": True,
                "position": {
                    "4": [18, 18, 15, 15],
                    "5.00": [12, 18],
                    "4.50": [14, 18],
                    "5.50": [18, 18, 12, 18],
                },
            },
        }
    },
]

NODE_SCRIPT = """
import fs from "node:fs";
const { AnnotationPlayer } = await import(process.argv[1]);
const data = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
const out = AnnotationPlayer.prototype.parseICLegacyAnnotations.call(AnnotationPlayer.prototype, data);
console.log(JSON.stringify(out.map((a) => {
  const shaped = { label: a.label ?? "", start: Number(a.start), end: Number(a.end), type: a.type };
  if (a.positions) shaped.positions = a.positions;
  return shaped;
})));
"""


@unittest.skipUnless(shutil.which("node"), "needs node")
class ParityWithAnnotationPlayerTests(unittest.TestCase):
    def js_conversion(self, path):
        completed = subprocess.run(
            [
                "node",
                "--input-type=module",
                "-e",
                NODE_SCRIPT,
                str(REPO_ROOT / "core/static/js/AnnotationPlayer.js"),
                str(path),
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        return json.loads(completed.stdout)

    def test_python_and_browser_convert_identically(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
            json.dump(SAMPLE, handle)
        paths = [Path(handle.name), *[p for p in FIXTURES if p.exists()]]
        try:
            for path in paths:
                with self.subTest(path=path.name):
                    expected = self.js_conversion(path)
                    self.assertEqual(
                        convert_legacy_annotations(json.loads(path.read_text())),
                        expected,
                    )
        finally:
            Path(handle.name).unlink()


class ConversionTests(unittest.TestCase):
    def test_types_and_carried_forward_sizes(self):
        converted = convert_legacy_annotations(SAMPLE)
        self.assertEqual(
            [a["type"] for a in converted],
            ["skip", "mute", "blank", "screenBlur", "blur"],
        )
        positions = converted[-1]["positions"]
        self.assertEqual([p["time"] for p in positions], [4.0, 4.5, 5.0, 5.5])
        self.assertEqual((positions[1]["width"], positions[1]["height"]), (15, 15))
        self.assertEqual((positions[3]["width"], positions[3]["height"]), (12, 18))


class ImportTests(ICTempDirsMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.editor = make_ic_editor()
        self.content = ContentFactory(
            playlist=PlaylistFactory(owner=self.editor, is_ic=True)
        )
        self.film_dir = self._ic_tmp / "Panda"
        (self.film_dir / ".ic").mkdir(parents=True)
        (self.film_dir / "panda.json").write_text(json.dumps(SAMPLE))
        (self.film_dir / "panda.icf").write_text(
            json.dumps(
                {"subtitle": None, "video": "panda.m4v", "annotation": "panda.json"}
            )
        )

    def test_import_creates_rows_the_player_reads_back(self):
        result = import_film(self.film_dir / "panda.icf", self.content)
        self.content.refresh_from_db()
        self.assertEqual(self.content.annotation_set, result.annotation_set)
        self.assertEqual(SkipAnnotation.objects.count(), 1)
        self.assertEqual(MuteAnnotation.objects.count(), 1)
        self.assertEqual(BlankAnnotation.objects.filter(type="k").count(), 1)
        self.assertEqual(BlankAnnotation.objects.filter(type="#").count(), 1)
        blur = BlurAnnotation.objects.get()
        self.assertEqual(
            BlurAnchorPoint.objects.filter(blur_annotation=blur).count(), 4
        )
        types = sorted(a["type"] for a in self.content.get_player_json()["annotations"])
        self.assertEqual(types, ["blank", "blank", "blur", "mute", "skip"])

    def test_dry_run_changes_nothing(self):
        import_film(self.film_dir / "panda.icf", self.content, dry_run=True)
        self.content.refresh_from_db()
        self.assertIsNone(self.content.annotation_set)
        self.assertEqual(SkipAnnotation.objects.count(), 0)

    def write_mapping(self, content_id):
        mapping = self._ic_tmp / "mapping.csv"
        with open(mapping, "w", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(["icf", "content_id"])
            writer.writerow(["Panda/panda.icf", content_id])
        return mapping

    def test_command_refuses_contents_outside_ic_playlists(self):
        outside = ContentFactory(playlist=PlaylistFactory(is_ic=False))
        with self.assertRaises(CommandError):
            call_command(
                "import_ic_legacy",
                self._ic_tmp,
                mapping=self.write_mapping(outside.pk),
                stdout=open("/dev/null", "w"),
                stderr=open("/dev/null", "w"),
            )

    @unittest.skipUnless(FFMPEG_AVAILABLE, "needs ffmpeg and ffprobe")
    def test_with_video_ingests_the_legacy_video_and_switches_the_content(self):
        from ic import jobs

        make_mp4(self.film_dir / ".ic" / "panda.m4v", seconds=2)
        call_command(
            "import_ic_legacy",
            self._ic_tmp,
            mapping=self.write_mapping(self.content.pk),
            with_video=True,
            stdout=open("/dev/null", "w"),
        )
        upload = ICUpload.objects.get()
        self.assertEqual(upload.job.kind, MediaJob.Kind.INGEST)
        while (job := jobs.claim_next()) is not None:
            jobs.run(job)
        self.content.refresh_from_db()
        master = self.content.resource.resource_files.get(is_projection_file=True)
        self.assertEqual(self.content.resource_file.derived_from, master)
