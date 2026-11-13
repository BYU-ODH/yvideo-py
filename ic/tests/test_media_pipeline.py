"""Upload → ingest → SD transcode → release, with real ffmpeg where it matters."""

import json
from pathlib import Path
import unittest
from unittest import mock

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core.factories import ContentFactory
from core.factories import ResourceFactory
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

    def test_resume_after_an_offset_mismatch(self):
        upload = self.start(8).json()
        self.assertEqual(self.put(upload, 0, b"1234").json()["received_bytes"], 4)
        conflict = self.put(upload, 0, b"1234")
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(conflict.json()["received_bytes"], 4)
        again = self.start(8).json()
        self.assertEqual(again["id"], upload["id"])
        self.assertEqual(again["received_bytes"], 4)
        # Finalizing early, or sending more than the declared size, is refused.
        self.assertEqual(self.client.post(upload["finalize_url"]).status_code, 409)
        self.assertEqual(self.put(upload, 4, b"567890").status_code, 400)
        self.assertEqual(self.put(upload, 4, b"5678").json()["received_bytes"], 8)

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

        # Replacing the master moves its contents to the new SD file; the old one goes.
        content = ContentFactory(resource_file=sd, resource=self.resource)
        old_sd_path = Path(sd.file.path)
        with self.captureOnCommitCallbacks(execute=True):
            self.upload_file(make_mp4(self._ic_tmp / "better.mp4", seconds=2, rate=25))
            run_due_jobs()
        content.refresh_from_db()
        new_master = ResourceFile.objects.get(
            resource=self.resource, is_projection_file=True
        )
        self.assertEqual(content.resource_file.derived_from, new_master)
        self.assertFalse(ResourceFile.objects.filter(pk=sd.pk).exists())
        self.assertFalse(old_sd_path.exists())

    @unittest.skipUnless(FFMPEG_AVAILABLE, "needs ffmpeg and ffprobe")
    def test_reuploading_the_same_master_is_refused_but_restores_a_released_one(self):
        source = make_mp4(self._ic_tmp / "source.mp4")
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

    def test_replacing_a_master_deletes_the_old_bytes_after_commit(self):
        previous = make_master(self.resource)
        previous_path = Path(previous.file.path)
        self.upload_file(self._write(b"a replacement master " * 400))
        probe = mock.Mock(apply_to=lambda resource_file: None)
        with (
            mock.patch.object(jobs.media, "probe", return_value=probe),
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

    def test_any_ingest_failure_fails_the_upload_and_frees_its_space(self):
        upload = self.upload_file(self._write(b"a master " * 400))
        with (
            mock.patch.object(
                jobs.media, "probe", return_value=mock.Mock(apply_to=lambda f: None)
            ),
            mock.patch.object(jobs, "file_xxh64", side_effect=OSError("disk error")),
        ):
            jobs.run(jobs.claim_next())
        row = ICUpload.objects.get(pk=upload["id"])
        self.assertEqual(row.status, ICUpload.Status.FAILED)
        self.assertIn("disk error", row.error)
        self.assertFalse(row.part_path.exists())

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
        self.assertFalse(jobs.release_if_ready(self.master))
        make_sd_file(self.master)
        self.verify_on_booth()
        path = Path(self.master.file.path)
        with self.captureOnCommitCallbacks(execute=True):
            self.assertTrue(jobs.release_if_ready(self.master))
        self.master.refresh_from_db()
        self.assertIsNotNone(self.master.master_released_at)
        self.assertFalse(path.exists())
