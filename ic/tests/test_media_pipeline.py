"""Upload → ingest → SD transcode → release, with real ffmpeg where it matters."""

import json
from pathlib import Path
import unittest
from unittest import mock

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core.factories import ResourceFactory
from core.factories import UserFactory
from core.models import ResourceAccess
from core.models import ResourceFile
from ic import jobs
from ic import media
from ic.models import ICInstance
from ic.models import ICInstanceFile
from ic.models import ICUpload
from ic.models import MediaJob
from ic.testing import FFMPEG_AVAILABLE
from ic.testing import ICTempDirsMixin
from ic.testing import make_ic_editor
from ic.testing import make_master
from ic.testing import make_mp4
from ic.testing import make_sd_file


@unittest.skipUnless(FFMPEG_AVAILABLE, "needs ffmpeg and ffprobe")
class MediaHelperTests(ICTempDirsMixin, TestCase):
    def test_faststart_detection_and_remux(self):
        slow = make_mp4(self._ic_tmp / "slow.mp4")
        self.assertFalse(media.is_faststart(slow))
        fixed = self._ic_tmp / "fixed.mp4"
        media.remux_faststart(slow, fixed)
        self.assertTrue(media.is_faststart(fixed))
        self.assertTrue(
            media.is_faststart(make_mp4(self._ic_tmp / "fast.mp4", faststart=True))
        )

    def test_probe_reads_codecs_and_geometry(self):
        result = media.probe(make_mp4(self._ic_tmp / "a.mp4", size="640x360", rate=25))
        self.assertEqual((result.width, result.height), (640, 360))
        self.assertEqual(result.video_codec, "h264")
        self.assertEqual(result.audio_codec, "aac")
        self.assertAlmostEqual(result.frame_rate, 25)
        self.assertAlmostEqual(result.duration_seconds, 2, delta=0.1)

    def test_probe_rejects_non_video(self):
        text = self._ic_tmp / "notes.mp4"
        text.write_text("not a video")
        with self.assertRaises(media.MediaError):
            media.probe(text)

    def test_sd_transcode_keeps_the_timeline(self):
        master = make_mp4(self._ic_tmp / "master.mp4", seconds=3, size="1280x720")
        sd = self._ic_tmp / "sd.mp4"
        media.transcode_sd(master, sd, duration_seconds=3)
        master_probe, sd_probe = media.probe(master), media.probe(sd)
        self.assertEqual(sd_probe.height, 480)
        media.assert_same_timeline(master_probe, sd_probe)
        self.assertTrue(media.is_faststart(sd))

    def test_timeline_mismatch_is_rejected(self):
        long = media.probe(make_mp4(self._ic_tmp / "long.mp4", seconds=3))
        short = media.probe(make_mp4(self._ic_tmp / "short.mp4", seconds=2))
        with self.assertRaises(media.MediaError):
            media.assert_same_timeline(long, short)


def run_due_jobs():
    while (job := jobs.claim_next()) is not None:
        jobs.run(job)


class UploadFlowTests(ICTempDirsMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.editor = make_ic_editor()
        self.resource = ResourceFactory(name="Amélie")
        ResourceAccess.objects.create(user=self.editor, resource=self.resource)
        self.client.force_login(self.editor)

    def start(self, size, filename="amelie.mp4"):
        return self.client.post(
            reverse("ic_upload_create", args=[self.resource.pk]),
            data=json.dumps({"filename": filename, "size": size}),
            content_type="application/json",
        )

    def put(self, upload, offset, data):
        return self.client.put(
            f"{upload['chunk_url']}?offset={offset}",
            data=data,
            content_type="application/octet-stream",
        )

    def upload_file(self, path):
        data = Path(path).read_bytes()
        upload = self.start(len(data)).json()
        half = len(data) // 2
        self.assertEqual(self.put(upload, 0, data[:half]).status_code, 200)
        self.assertEqual(self.put(upload, half, data[half:]).status_code, 200)
        response = self.client.post(upload["finalize_url"])
        self.assertEqual(response.status_code, 200, response.content)
        return upload

    def test_requires_ic_role_and_resource_access(self):
        self.client.force_login(UserFactory(instructor=True))
        self.assertEqual(self.start(10).status_code, 403)
        outsider = make_ic_editor()
        self.client.force_login(outsider)
        self.assertEqual(self.start(10).status_code, 403)

    def test_only_mp4_files(self):
        self.assertEqual(self.start(10, filename="film.mkv").status_code, 400)

    def test_resume_after_an_offset_mismatch(self):
        upload = self.start(8).json()
        self.assertEqual(self.put(upload, 0, b"1234").json()["received_bytes"], 4)
        conflict = self.put(upload, 0, b"1234")
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(conflict.json()["received_bytes"], 4)
        again = self.start(8).json()
        self.assertEqual(again["id"], upload["id"])
        self.assertEqual(again["received_bytes"], 4)
        self.assertEqual(self.put(upload, 4, b"5678").json()["received_bytes"], 8)

    def test_more_data_than_declared_is_refused(self):
        upload = self.start(4).json()
        self.assertEqual(self.put(upload, 0, b"123456").status_code, 400)
        self.assertEqual(
            ICUpload.objects.get(pk=upload["id"]).part_path.stat().st_size, 0
        )

    def test_finalize_before_all_bytes_arrive_is_refused(self):
        upload = self.start(8).json()
        self.put(upload, 0, b"1234")
        self.assertEqual(self.client.post(upload["finalize_url"]).status_code, 409)

    def test_someone_elses_upload_is_off_limits(self):
        upload = self.start(8).json()
        other = make_ic_editor()
        ResourceAccess.objects.create(user=other, resource=self.resource)
        self.client.force_login(other)
        self.assertEqual(self.put(upload, 0, b"1234").status_code, 403)

    @unittest.skipUnless(FFMPEG_AVAILABLE, "needs ffmpeg and ffprobe")
    def test_ingest_and_transcode_create_master_and_sd_file(self):
        source = make_mp4(self._ic_tmp / "source.mp4", seconds=2, size="1280x720")
        upload = self.upload_file(source)
        run_due_jobs()
        upload_row = ICUpload.objects.get(pk=upload["id"])
        self.assertEqual(upload_row.status, ICUpload.Status.DONE, upload_row.error)
        master = ResourceFile.objects.get(
            resource=self.resource, is_projection_file=True
        )
        self.assertTrue(master.faststart)
        self.assertTrue(media.is_faststart(master.file.path))
        self.assertTrue(Path(master.file.path).is_relative_to(self.projection_root))
        self.assertEqual(master.height, 720)
        sd = master.derivatives.get()
        self.assertEqual(sd.height, 480)
        self.assertTrue(Path(sd.file.path).is_relative_to(self.media_root))
        self.assertFalse(sd.is_projection_file)
        self.assertEqual(
            MediaJob.objects.get(kind=MediaJob.Kind.TRANSCODE).status,
            MediaJob.Status.SUCCEEDED,
        )

    @unittest.skipUnless(FFMPEG_AVAILABLE, "needs ffmpeg and ffprobe")
    def test_reuploading_the_same_master_is_refused_but_restores_a_released_one(self):
        source = make_mp4(self._ic_tmp / "source.mp4", faststart=True)
        self.upload_file(source)
        run_due_jobs()
        master = ResourceFile.objects.get(
            resource=self.resource, is_projection_file=True
        )
        second = self.upload_file(source)
        run_due_jobs()
        self.assertEqual(
            ICUpload.objects.get(pk=second["id"]).status, ICUpload.Status.FAILED
        )

        Path(master.file.path).unlink()
        master.master_released_at = timezone.now()
        master.save()
        third = self.upload_file(source)
        run_due_jobs()
        self.assertEqual(
            ICUpload.objects.get(pk=third["id"]).status, ICUpload.Status.DONE
        )
        master.refresh_from_db()
        self.assertIsNone(master.master_released_at)
        self.assertTrue(Path(master.file.path).exists())

    def test_a_failed_ingest_keeps_the_previous_master(self):
        previous = make_master(self.resource)
        previous_path = Path(previous.file.path)
        upload = self.upload_file(self._write(b"a replacement master " * 400))
        probe = media.ProbeResult(
            duration_seconds=100.0,
            width=1920,
            height=1080,
            frame_rate=24.0,
            video_codec="h264",
            audio_codec="aac",
            bitrate=10_000_000,
            color_transfer="",
            video_start_time=0.0,
            video_duration=100.0,
            raw={},
        )
        with (
            mock.patch.object(jobs.media, "probe", return_value=probe),
            mock.patch.object(jobs.media, "is_faststart", return_value=True),
            mock.patch.object(jobs, "queue", side_effect=RuntimeError("disk on fire")),
        ):
            job = jobs.claim_next()
            jobs.run(job)
        self.assertEqual(job.status, MediaJob.Status.FAILED)
        self.assertTrue(ResourceFile.objects.filter(pk=previous.pk).exists())
        self.assertTrue(previous_path.exists())
        # The uploaded bytes are back where a re-run of the job looks for them.
        row = ICUpload.objects.get(pk=upload["id"])
        self.assertTrue(row.part_path.exists())
        self.assertEqual(
            ResourceFile.objects.filter(
                resource=self.resource, is_projection_file=True
            ).count(),
            1,
        )

    def test_replacing_a_master_deletes_the_old_bytes_after_commit(self):
        previous = make_master(self.resource)
        previous_path = Path(previous.file.path)
        self.upload_file(self._write(b"a replacement master " * 400))
        probe = mock.Mock(apply_to=lambda resource_file: None)
        with (
            mock.patch.object(jobs.media, "probe", return_value=probe),
            mock.patch.object(jobs.media, "is_faststart", return_value=True),
            self.captureOnCommitCallbacks(execute=True),
        ):
            job = jobs.claim_next()
            jobs.run(job)
        self.assertEqual(job.status, MediaJob.Status.SUCCEEDED, job.error)
        self.assertFalse(ResourceFile.objects.filter(pk=previous.pk).exists())
        self.assertFalse(previous_path.exists())
        master = ResourceFile.objects.get(
            resource=self.resource, is_projection_file=True
        )
        self.assertTrue(Path(master.file.path).exists())

    def _write(self, data):
        path = self._ic_tmp / "upload.mp4"
        path.write_bytes(data)
        return path

    @unittest.skipUnless(FFMPEG_AVAILABLE, "needs ffmpeg and ffprobe")
    def test_a_file_that_is_not_a_video_fails_with_a_message(self):
        junk = self._ic_tmp / "junk.mp4"
        junk.write_bytes(b"\x00" * 2048)
        upload = self.upload_file(junk)
        run_due_jobs()
        row = ICUpload.objects.get(pk=upload["id"])
        self.assertEqual(row.status, ICUpload.Status.FAILED)
        self.assertTrue(row.error)
        self.assertFalse(row.part_path.exists())


class ReleaseMasterTests(ICTempDirsMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.master = make_master(ResourceFactory())
        self.booth = ICInstance(name="booth")
        self.booth.issue_token()
        self.booth.save()

    def verify_on_booth(self, hashed=True, checksum=None):
        ICInstanceFile.objects.update_or_create(
            instance=self.booth,
            resource_file=self.master,
            defaults={
                "xxh64": checksum or self.master.checksum,
                "verified_at": timezone.now(),
                "hashed_from_bytes": hashed,
            },
        )

    def test_blockers_until_sd_exists_and_the_booth_hashed_its_copy(self):
        self.assertIn(
            "the SD transcode has not finished", jobs.release_blockers(self.master)
        )
        make_sd_file(self.master)
        self.assertIn(
            "booth has not verified its copy yet", jobs.release_blockers(self.master)
        )
        self.verify_on_booth(hashed=False)
        self.assertTrue(jobs.release_blockers(self.master))
        self.verify_on_booth(checksum="ffffffffffffffff")
        self.assertTrue(jobs.release_blockers(self.master))
        self.verify_on_booth()
        self.assertEqual(jobs.release_blockers(self.master), [])

    def test_release_deletes_the_bytes_and_keeps_the_row(self):
        make_sd_file(self.master)
        self.verify_on_booth()
        job = jobs.maybe_queue_release(self.master)
        self.assertIsNotNone(job)
        self.assertEqual(jobs.maybe_queue_release(self.master), job)  # not queued twice
        path = Path(self.master.file.path)
        run_due_jobs()
        self.master.refresh_from_db()
        self.assertIsNotNone(self.master.master_released_at)
        self.assertFalse(path.exists())
        self.assertTrue(ResourceFile.objects.filter(pk=self.master.pk).exists())
        self.assertEqual(self.master.checksum, self.master.checksum)

    def test_release_rechecks_conditions_when_it_runs(self):
        make_sd_file(self.master)
        self.verify_on_booth()
        jobs.maybe_queue_release(self.master)
        ICInstanceFile.objects.all().delete()
        run_due_jobs()
        self.master.refresh_from_db()
        self.assertIsNone(self.master.master_released_at)
        self.assertTrue(Path(self.master.file.path).exists())
        self.assertEqual(
            MediaJob.objects.get(kind=MediaJob.Kind.RELEASE_MASTER).status,
            MediaJob.Status.FAILED,
        )
