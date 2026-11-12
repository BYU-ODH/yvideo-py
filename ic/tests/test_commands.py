from datetime import timedelta
from io import StringIO
from unittest import mock

from django.core.management import CommandError
from django.core.management import call_command
from django.test import TestCase
from django.test import override_settings
from django.utils import timezone

from ic import booth_state
from ic.models import ICBoothState
from ic.models import ICLocalFile
from ic.models import ICSyncRun
from ic.models import MediaJob
from ic.testing import ICTempDirsMixin


class SyncAgentCommandTests(ICTempDirsMixin, TestCase):
    def test_refuses_to_run_on_production(self):
        with self.assertRaises(CommandError):
            call_command("ic_sync_agent", "--once")

    @override_settings(IS_IC_INSTANCE=True)
    def test_once_runs_queued_forced_runs_first_then_a_poll(self):
        stale = ICSyncRun.objects.create(
            trigger=ICSyncRun.Trigger.POLL, status=ICSyncRun.Status.RUNNING
        )
        event = ICSyncRun.objects.create(trigger=ICSyncRun.Trigger.EVENT)
        forced = ICSyncRun.objects.create(trigger=ICSyncRun.Trigger.FORCE_PULL)
        order = []

        def fake_execute(run):
            order.append(run.trigger)
            run.status = ICSyncRun.Status.SUCCEEDED
            run.save()
            return run

        with mock.patch(
            "ic.management.commands.ic_sync_agent.execute_run", side_effect=fake_execute
        ):
            call_command("ic_sync_agent", "--once")
        self.assertEqual(order, ["force_pull", "event", "poll"])
        stale.refresh_from_db()
        self.assertEqual(stale.status, ICSyncRun.Status.FAILED)
        self.assertTrue(booth_state.agent_alive())
        self.assertTrue(
            ICSyncRun.objects.filter(pk=forced.pk, status="succeeded").exists()
        )
        self.assertTrue(
            ICSyncRun.objects.filter(pk=event.pk, status="succeeded").exists()
        )

    @override_settings(IS_IC_INSTANCE=True)
    def test_weekly_rehash_of_only_copies(self):
        ICLocalFile.objects.create(
            kind="projection",
            ref_id=1,
            name="ic_projection/1/a.mp4",
            size=1,
            xxh64="0",
            verified_at=timezone.now(),
            available_from_parent=False,
        )
        runs = []

        def fake_execute(run):
            runs.append(run.options)
            run.status = ICSyncRun.Status.SUCCEEDED
            run.save()
            return run

        morning = timezone.localtime().replace(hour=5)
        with (
            mock.patch(
                "ic.management.commands.ic_sync_agent.execute_run",
                side_effect=fake_execute,
            ),
            mock.patch(
                "ic.management.commands.ic_sync_agent.timezone.localtime",
                return_value=morning,
            ),
        ):
            call_command("ic_sync_agent", "--once")
            state = ICBoothState.get()
            state.last_full_rehash_at = timezone.now()
            state.save()
            call_command("ic_sync_agent", "--once")
        self.assertEqual(runs, [{"full_rehash": True}, {}])


class MediaWorkerCommandTests(ICTempDirsMixin, TestCase):
    def test_once_runs_due_jobs_and_requeues_interrupted_ones(self):
        interrupted = MediaJob.objects.create(
            kind=MediaJob.Kind.CLEANUP_UPLOADS, status=MediaJob.Status.RUNNING
        )
        later = MediaJob.objects.create(
            kind=MediaJob.Kind.IC_NOTIFY, not_before=timezone.now() + timedelta(hours=1)
        )
        with mock.patch("os.nice"):
            call_command("process_media_jobs", "--once", stdout=StringIO())
        interrupted.refresh_from_db()
        self.assertEqual(interrupted.status, MediaJob.Status.SUCCEEDED)
        later.refresh_from_db()
        self.assertEqual(later.status, MediaJob.Status.QUEUED)


class SeedICDemoTests(ICTempDirsMixin, TestCase):
    def test_seeds_a_ready_program_from_the_demo_videos(self):
        from ic import readiness
        from ic.models import ICProgram

        with self.assertRaises(CommandError):
            call_command("seed_ic_demo", stdout=StringIO())  # DEBUG is off in tests
        with self.assertRaises(CommandError):
            call_command(
                "seed_ic_demo", "--force", stdout=StringIO()
            )  # no demo admin yet
        call_command("seed_demo_data", "--force", stdout=StringIO())
        call_command("seed_ic_demo", "--force", stdout=StringIO())
        call_command("seed_ic_demo", "--force", stdout=StringIO())  # re-runnable
        program = ICProgram.objects.get(name="IC demo program")
        problems = readiness.program_problems(program)
        self.assertNotEqual(readiness.rollup(problems), readiness.ERROR, problems)
        self.assertEqual(program.items.count(), 2)
