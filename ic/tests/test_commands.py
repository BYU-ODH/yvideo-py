from datetime import timedelta
from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.test import TestCase
from django.test import override_settings
from django.utils import timezone

from core.factories import ResourceFactory
from ic.models import ICBoothState
from ic.models import ICSyncRun
from ic.models import ICUpload
from ic.models import MediaJob
from ic.testing import ICTempDirsMixin


class SyncAgentCommandTests(ICTempDirsMixin, TestCase):
    @override_settings(IS_IC_INSTANCE=True)
    def test_once_syncs_at_startup_with_sync_now_first(self):
        stale = ICSyncRun.objects.create(
            trigger=ICSyncRun.Trigger.AUTO, status=ICSyncRun.Status.RUNNING
        )
        order = []

        def fake_check(client):
            ICSyncRun.objects.create(trigger=ICSyncRun.Trigger.FORCE_PULL)

        def fake_execute(run, **kwargs):
            order.append(run.trigger)
            # Runs keep the agent alive and checking through their heartbeat.
            self.assertTrue(callable(kwargs["heartbeat"]))
            run.status = ICSyncRun.Status.SUCCEEDED
            run.save()
            return run

        with (
            mock.patch(
                "ic.management.commands.ic_sync_agent.check_parent",
                side_effect=fake_check,
            ) as checked,
            mock.patch(
                "ic.management.commands.ic_sync_agent.execute_run",
                side_effect=fake_execute,
            ),
        ):
            call_command("ic_sync_agent", "--once")
        checked.assert_called_once()
        # The startup run was queued first, but "Sync now" jumps ahead of it.
        self.assertEqual(order, ["force_pull", "auto"])
        stale.refresh_from_db()
        self.assertEqual(stale.status, ICSyncRun.Status.FAILED)
        self.assertTrue(ICBoothState.get().agent_alive)


class MediaWorkerCommandTests(ICTempDirsMixin, TestCase):
    def test_once_requeues_interrupted_jobs_and_cleans_up_abandoned_uploads(self):
        interrupted = MediaJob.objects.create(
            kind=MediaJob.Kind.TRANSCODE,
            status=MediaJob.Status.RUNNING,
            payload={"master_id": 0},
        )
        upload = ICUpload.objects.create(
            resource=ResourceFactory(), filename="film.mp4", expected_size=10
        )
        upload.part_path.parent.mkdir(parents=True, exist_ok=True)
        upload.part_path.write_bytes(b"12345")
        ICUpload.objects.filter(pk=upload.pk).update(
            updated_at=timezone.now() - timedelta(days=8)
        )
        call_command("process_media_jobs", "--once", stdout=StringIO())
        interrupted.refresh_from_db()
        # It ran again (and failed, since master 0 doesn't exist) instead of staying stuck.
        self.assertEqual(interrupted.status, MediaJob.Status.FAILED)
        self.assertFalse(ICUpload.objects.filter(pk=upload.pk).exists())
        self.assertFalse(upload.part_path.exists())
        # Cleanup isn't a job, so it doesn't crowd the dashboard's job list.
        self.assertEqual(MediaJob.objects.count(), 1)
