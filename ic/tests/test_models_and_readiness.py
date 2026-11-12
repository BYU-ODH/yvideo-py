from datetime import timedelta
from pathlib import Path

from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.db import transaction
from django.test import TestCase
from django.test import override_settings
from django.utils import timezone

from core.factories import ContentFactory
from core.factories import PlaylistFactory
from core.factories import ResourceFactory
from core.factories import UserFactory
from core.models import ResourceFile
from ic import readiness
from ic.models import ICProgram
from ic.models import ICProgramItem
from ic.models import MediaJob
from ic.testing import ICTempDirsMixin
from ic.testing import make_ic_editor
from ic.testing import make_master
from ic.testing import make_program_scenario


class ICEditorRoleTests(TestCase):
    def test_group_members_and_superusers_are_ic_editors(self):
        self.assertTrue(make_ic_editor().is_ic_editor)
        self.assertTrue(UserFactory(admin=True).is_ic_editor)
        self.assertFalse(UserFactory(instructor=True).is_ic_editor)
        self.assertFalse(UserFactory(lab_assistant=True).is_ic_editor)


class ProjectionFileModelTests(ICTempDirsMixin, TestCase):
    def test_masters_are_stored_outside_media_root(self):
        master = make_master(ResourceFactory())
        path = Path(master.file.path)
        self.assertTrue(path.is_relative_to(self.projection_root))
        self.assertFalse(path.is_relative_to(self.media_root))
        self.assertTrue(path.exists())

    def test_one_projection_master_per_resource(self):
        resource = ResourceFactory()
        make_master(resource)
        with self.assertRaises(IntegrityError), transaction.atomic():
            make_master(resource, payload=b"another master" * 100)

    def test_a_master_cannot_be_attached_to_content(self):
        master = make_master(ResourceFactory())
        content = ContentFactory.build(
            playlist=PlaylistFactory(), resource_file=master, resource=master.resource
        )
        with self.assertRaises(ValidationError):
            content.clean()

    def test_deleting_a_released_master_row_does_not_fail(self):
        master = make_master(ResourceFactory())
        Path(master.file.path).unlink()
        master.delete()
        self.assertFalse(ResourceFile.objects.filter(pk=master.pk).exists())


class ProgramDateTests(TestCase):
    def test_active_upcoming_and_syncable(self):
        today = timezone.localdate()
        past = ICProgram.objects.create(
            name="past",
            start_date=today - timedelta(days=9),
            end_date=today - timedelta(days=2),
        )
        yesterday = ICProgram.objects.create(
            name="yesterday",
            start_date=today - timedelta(days=3),
            end_date=today - timedelta(days=1),
        )
        now = ICProgram.objects.create(
            name="now", start_date=today, end_date=today + timedelta(days=3)
        )
        later = ICProgram.objects.create(
            name="later",
            start_date=today + timedelta(days=4),
            end_date=today + timedelta(days=6),
        )
        self.assertEqual(list(ICProgram.active(today)), [now])
        self.assertEqual(set(ICProgram.syncable(today)), {yesterday, now, later})
        self.assertNotIn(past, ICProgram.syncable(today))
        self.assertTrue(later.is_upcoming(today))
        self.assertTrue(past.is_past(today))

    def test_end_cannot_precede_start(self):
        today = timezone.localdate()
        with self.assertRaises(IntegrityError), transaction.atomic():
            ICProgram.objects.create(
                name="bad", start_date=today, end_date=today - timedelta(days=1)
            )


class ReadinessTests(ICTempDirsMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.scenario = make_program_scenario()
        self.film = self.scenario["film_item"]

    def codes(self, item):
        item.refresh_from_db()
        return {p.code for p in readiness.item_problems(item)}

    def test_complete_scenario_is_ready(self):
        problems = readiness.program_problems(self.scenario["program"])
        self.assertEqual(readiness.rollup(problems), "ready", problems)

    def test_missing_master(self):
        self.film.projection_file().delete()
        self.assertIn("no_master", self.codes(self.film))

    def test_url_only_content_needs_the_network(self):
        content = self.film.content
        content.resource_file = None
        content.resource = None
        content.url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
        content.save()
        self.assertIn("needs_network", self.codes(self.film))

    def test_av1_and_unverified_codecs(self):
        master = self.film.projection_file()
        master.video_codec = "av1"
        master.save()
        self.assertIn("codec", self.codes(self.film))
        master.video_codec = "hevc"
        master.save()
        self.assertIn("codec", self.codes(self.film))
        with override_settings(IC_BOOTH_VIDEO_CODECS=["h264", "hevc"]):
            self.assertNotIn("codec", self.codes(self.film))

    def test_hdr_is_a_warning(self):
        master = self.film.projection_file()
        master.color_transfer = "smpte2084"
        master.save()
        problems = readiness.item_problems(self.film)
        hdr = [p for p in problems if p.code == "hdr"]
        self.assertEqual(len(hdr), 1)
        self.assertFalse(hdr[0].is_error)

    def test_above_verified_maximum_is_a_warning(self):
        with override_settings(
            IC_BOOTH_VERIFIED_MAX={"height": 720, "frame_rate": None, "bitrate": None}
        ):
            problems = readiness.item_problems(self.film)
        self.assertIn("above_verified", {p.code for p in problems})
        self.assertEqual(readiness.rollup(problems), "warning")

    def test_annotations_on_a_file_not_made_from_the_master(self):
        content = self.film.content
        other = ResourceFile.objects.create(
            resource=content.resource,
            version="dvd rip",
            file="x/dvd.mp4",
            checksum="0123456789abcdef",
        )
        content.resource_file = other
        content.save()
        self.assertIn("timing_unverified", self.codes(self.film))
        self.film.timing_verified = True
        self.film.save()
        self.assertNotIn("timing_unverified", self.codes(self.film))

    def test_vtt_without_a_default_track(self):
        content = self.film.content
        content.default_subtitle_track = None
        content.save()
        self.assertIn("no_vtt", self.codes(self.film))

    def test_pending_and_failed_transcodes(self):
        master = self.film.projection_file()
        job = MediaJob.objects.create(
            kind=MediaJob.Kind.TRANSCODE, resource_file=master
        )
        self.assertIn("transcode_pending", self.codes(self.film))
        job.status = MediaJob.Status.FAILED
        job.error = "boom"
        job.save()
        master.derivatives.update(derived_from=None)
        self.assertIn("transcode_failed", self.codes(self.film))

    def test_released_master_without_a_booth_copy_is_lost(self):
        master = self.film.projection_file()
        master.master_released_at = timezone.now()
        master.save()
        self.assertIn("master_lost", self.codes(self.film))

    def test_empty_program(self):
        program = ICProgram.objects.create(
            name="empty", start_date=timezone.localdate(), end_date=timezone.localdate()
        )
        self.assertEqual(
            readiness.rollup(readiness.program_problems(program)), readiness.ERROR
        )

    def test_item_projection_file(self):
        self.assertEqual(
            self.film.projection_file(),
            self.film.content.resource.resource_files.get(is_projection_file=True),
        )
        self.assertEqual(self.scenario["loop_item"].mode, ICProgramItem.Mode.LOOP)
