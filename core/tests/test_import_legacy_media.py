from io import BytesIO
from io import StringIO
import json
from pathlib import Path
import shutil
import tempfile
from unittest import mock

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.test import override_settings

from ..factories import ContentFactory
from ..factories import ResourceFactory
from ..factories import ResourceFileFactory
from ..factories import SubtitleFactory
from ..legacy_migration import LegacySourceMap
from ..legacy_migration import io_limits
from ..legacy_migration.file_index import compute_checksum
from ..legacy_migration.io_limits import read_chunks
from ..models import Content
from ..models import ResourceFile


@mock.patch("core.management.commands.import_legacy_media.lower_process_priority")
class ImportLegacyMediaTests(TestCase):
    def setUp(self):
        root = Path(tempfile.mkdtemp(prefix="import-legacy-media-"))
        self.addCleanup(shutil.rmtree, root)
        self.media_root = root / "media"
        self.legacy_root = root / "legacy"
        self.media_root.mkdir()
        self.legacy_root.mkdir()
        settings_override = override_settings(
            MEDIA_ROOT=str(self.media_root),
            LEGACY_MIGRATION_MEDIA_ROOT=str(self.legacy_root),
        )
        settings_override.enable()
        self.addCleanup(settings_override.disable)

    def placeholder_file(self, legacy_id, filepath, payload=None):
        if payload is not None:
            (self.legacy_root / filepath).write_bytes(payload)
        resource_file = ResourceFileFactory(
            file="", resource=ResourceFactory(), version="english"
        )
        content = ContentFactory(resource_file=resource_file)
        LegacySourceMap.objects.create(
            source_type="file",
            source_id=legacy_id,
            target_model="ResourceFile",
            target_id=resource_file.pk,
            metadata={"source_row": {"filepath": filepath}, "media_pending": True},
        )
        return resource_file, content

    def placeholder_subtitle(self, legacy_id, cues):
        subtitle = SubtitleFactory(subtitles_file="")
        LegacySourceMap.objects.create(
            source_type="subtitle",
            source_id=legacy_id,
            target_model="Subtitle",
            target_id=subtitle.pk,
            metadata={
                "source_row": {"content": json.dumps(cues)},
                "media_pending": True,
            },
        )
        return subtitle

    def run_command(self, *args):
        stdout, stderr = StringIO(), StringIO()
        call_command("import_legacy_media", *args, stdout=stdout, stderr=stderr)
        return stdout.getvalue(), stderr.getvalue()

    def pending(self):
        return LegacySourceMap.objects.filter(metadata__media_pending=True).count()

    def test_dry_run_reports_plan_without_changes(self, _priority):
        resource_file, _ = self.placeholder_file("f1", "a.mp4", b"video-a")
        self.placeholder_subtitle("s1", [{"start": 0, "end": 1, "text": "Hi"}])

        stdout, _ = self.run_command()

        self.assertIn("file f1: would link", stdout)
        self.assertIn("subtitle s1: would write subtitle", stdout)
        resource_file.refresh_from_db()
        self.assertFalse(resource_file.file)
        self.assertEqual(self.pending(), 2)

    def test_commit_links_files_writes_subtitles_and_is_rerunnable(self, _priority):
        resource_file, _ = self.placeholder_file("f1", "a.mp4", b"video-a")
        subtitle = self.placeholder_subtitle(
            "s1", [{"start": 0, "end": 1, "text": "Hi"}]
        )

        stdout, _ = self.run_command("--commit")

        self.assertIn("file f1: link", stdout)
        resource_file.refresh_from_db()
        placed = Path(resource_file.file.path)
        self.assertEqual(
            placed.stat().st_ino, (self.legacy_root / "a.mp4").stat().st_ino
        )
        self.assertEqual(resource_file.checksum, compute_checksum(placed))
        subtitle.refresh_from_db()
        self.assertIn("Hi", subtitle.subtitles_file.read().decode())
        self.assertEqual(self.pending(), 0)

        stdout, _ = self.run_command("--commit")
        self.assertIn("nothing pending", stdout)

    def test_missing_source_is_skipped_and_stays_pending(self, _priority):
        self.placeholder_file("missing", "gone.mp4")
        self.placeholder_file("present", "b.mp4", b"video-b")

        stdout, stderr = self.run_command("--commit")

        self.assertIn("file missing: skipped", stderr)
        self.assertIn("file present: link", stdout)
        self.assertEqual(
            list(
                LegacySourceMap.objects.filter(
                    metadata__media_pending=True
                ).values_list("source_id", flat=True)
            ),
            ["missing"],
        )

    def test_duplicate_bytes_in_same_resource_reuse_existing_file(self, _priority):
        first, _ = self.placeholder_file("f1", "a.mp4", b"same-bytes")
        (self.legacy_root / "copy.mp4").write_bytes(b"same-bytes")
        second = ResourceFileFactory(
            file="", resource=first.resource, version="spanish"
        )
        content = ContentFactory(resource_file=second)
        LegacySourceMap.objects.create(
            source_type="file",
            source_id="f2",
            target_model="ResourceFile",
            target_id=second.pk,
            metadata={"source_row": {"filepath": "copy.mp4"}, "media_pending": True},
        )

        stdout, _ = self.run_command("--commit")

        self.assertIn(f"file f2: duplicate of ResourceFile {first.pk}", stdout)
        self.assertFalse(ResourceFile.objects.filter(pk=second.pk).exists())
        content.refresh_from_db()
        self.assertEqual(content.resource_file_id, first.pk)
        self.assertEqual(
            LegacySourceMap.objects.get(source_id="f2").target_id, first.pk
        )
        self.assertFalse(
            (self.media_root / first.resource.name / "spanish.mp4").exists()
        )

    def test_duplicate_bytes_in_other_resource_left_for_review(self, _priority):
        first, _ = self.placeholder_file("f1", "a.mp4", b"same-bytes")
        (self.legacy_root / "copy.mp4").write_bytes(b"same-bytes")
        second, content = self.placeholder_file("f2", "copy.mp4")

        _, stderr = self.run_command("--commit")

        self.assertIn("file f2: skipped", stderr)
        self.assertIn("another resource", stderr)
        content.refresh_from_db()
        self.assertEqual(content.resource_file_id, second.pk)
        self.assertTrue(Content.objects.filter(resource_file=first).exists())
        self.assertFalse((self.media_root / second.resource.name).exists())
        self.assertEqual(self.pending(), 1)

    def test_existing_subtitle_file_is_not_overwritten(self, _priority):
        subtitle = SubtitleFactory()
        original = subtitle.subtitles_file.read()
        LegacySourceMap.objects.create(
            source_type="subtitle",
            source_id="s1",
            target_model="Subtitle",
            target_id=subtitle.pk,
            metadata={
                "source_row": {"content": json.dumps([{"text": "Legacy"}])},
                "media_pending": True,
            },
        )

        stdout, _ = self.run_command("--commit")

        self.assertIn("subtitle s1: already has media", stdout)
        subtitle.refresh_from_db()
        self.assertEqual(subtitle.subtitles_file.read(), original)
        self.assertEqual(self.pending(), 0)

    def test_unlinkable_file_is_skipped(self, _priority):
        self.placeholder_file("f1", "a.mp4", b"video-a")

        with mock.patch("os.link", side_effect=OSError("Operation not permitted")):
            _, stderr = self.run_command("--commit")

        self.assertIn("file f1: skipped: Cannot hardlink", stderr)
        self.assertEqual(list(self.media_root.rglob("*.mp4")), [])
        self.assertEqual(self.pending(), 1)

    @override_settings(LEGACY_MIGRATION_MEDIA_ROOT="yvideo:/opt/y-video")
    def test_remote_legacy_root_is_refused(self, _priority):
        with self.assertRaisesMessage(CommandError, "must be a local path"):
            self.run_command()


class ReadLimitTests(TestCase):
    def test_reads_pause_to_stay_under_limit(self):
        handle = BytesIO(b"x" * 3 * 1024 * 1024)
        with (
            mock.patch.object(io_limits, "max_read_mbps", 1),
            mock.patch(
                "core.legacy_migration.io_limits.time.monotonic", return_value=0
            ),
            mock.patch("core.legacy_migration.io_limits.time.sleep") as sleep,
        ):
            chunks = list(read_chunks(handle))

        self.assertEqual(len(chunks), 3)
        self.assertAlmostEqual(sleep.call_args.args[0], 3 * 1024 * 1024 / 1_000_000)
