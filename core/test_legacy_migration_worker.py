import fcntl
from pathlib import Path
import runpy
import subprocess
import tempfile
from types import SimpleNamespace
from unittest import mock
import uuid

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase
from django.test import TestCase
from django.test import override_settings
from django.utils import timezone

from .factories import UserFactory
from .legacy_migration import LegacyMigrationJob
from .legacy_migration import LegacyMigrationJobStatus
from .legacy_migration import LegacyMigrationJobType
from .legacy_migration import LegacyMigrationRequest
from .legacy_migration import LegacyMigrationService
from .legacy_migration import LegacyMigrationStatus


class LegacyMigrationRecoveryTests(TestCase):
    def test_recover_running_jobs_requeues_and_logs_interrupted_jobs(self):
        owner = UserFactory(instructor=True)
        preflight_request = LegacyMigrationRequest.objects.create(
            requested_by=owner,
            target_owner=owner,
            migration_kind="collection",
            legacy_reference=str(uuid.uuid4()),
            status=LegacyMigrationStatus.RUNNING,
        )
        import_request = LegacyMigrationRequest.objects.create(
            requested_by=owner,
            target_owner=owner,
            migration_kind="collection",
            legacy_reference=str(uuid.uuid4()),
            status=LegacyMigrationStatus.RUNNING,
        )
        preflight_job = LegacyMigrationJob.objects.create(
            request=preflight_request,
            job_type=LegacyMigrationJobType.PREFLIGHT,
            status=LegacyMigrationJobStatus.RUNNING,
            current_phase="snapshot",
            attempts=1,
            started_at=timezone.now(),
            log=[{"phase": "snapshot", "timestamp": timezone.now().isoformat()}],
        )
        import_job = LegacyMigrationJob.objects.create(
            request=import_request,
            job_type=LegacyMigrationJobType.IMPORT,
            status=LegacyMigrationJobStatus.RUNNING,
            current_phase="files",
            attempts=2,
            started_at=timezone.now(),
            log=[{"phase": "files", "timestamp": timezone.now().isoformat()}],
        )

        service = LegacyMigrationService(require_catalog=False)
        with self.assertLogs("core.legacy_migration.service", level="WARNING") as logs:
            recovered_count = service.recover_running_jobs()

        self.assertEqual(recovered_count, 2)
        self.assertTrue(
            any(
                "Recovered 2 interrupted legacy migration job(s)." in message
                for message in logs.output
            )
        )

        preflight_job.refresh_from_db()
        preflight_request.refresh_from_db()
        self.assertEqual(preflight_job.status, LegacyMigrationJobStatus.QUEUED)
        self.assertIsNone(preflight_job.started_at)
        self.assertEqual(preflight_job.current_phase, "")
        self.assertEqual(preflight_job.attempts, 1)
        self.assertEqual(preflight_job.log[-1]["event"], "recovered")
        self.assertEqual(preflight_job.log[-1]["previous_phase"], "snapshot")
        self.assertEqual(preflight_request.status, LegacyMigrationStatus.SUBMITTED)

        import_job.refresh_from_db()
        import_request.refresh_from_db()
        self.assertEqual(import_job.status, LegacyMigrationJobStatus.QUEUED)
        self.assertIsNone(import_job.started_at)
        self.assertEqual(import_job.current_phase, "")
        self.assertEqual(import_job.attempts, 2)
        self.assertEqual(import_job.log[-1]["event"], "recovered")
        self.assertEqual(import_job.log[-1]["previous_phase"], "files")
        self.assertEqual(import_request.status, LegacyMigrationStatus.QUEUED)


class LegacyMigrationWorkerCommandTests(SimpleTestCase):
    def test_worker_recovers_running_jobs_before_processing_queue(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            lock_path = Path(temp_dir) / "job_worker.lock"
            service = mock.Mock()
            service.recover_running_jobs.return_value = 1
            service.run_next_job.return_value = None

            with (
                override_settings(LEGACY_MIGRATION_WORKER_LOCK_PATH=lock_path),
                mock.patch(
                    "core.management.commands.process_legacy_migration_jobs."
                    "LegacyMigrationService",
                    return_value=service,
                ),
                self.assertLogs(
                    "core.management.commands.process_legacy_migration_jobs",
                    level="INFO",
                ) as logs,
            ):
                call_command("process_legacy_migration_jobs", "--once")

        service.recover_running_jobs.assert_called_once_with()
        service.run_next_job.assert_called_once_with()
        self.assertTrue(
            any(
                "startup recovery finished; recovered=1" in line for line in logs.output
            )
        )
        self.assertTrue(any("worker stopped" in line for line in logs.output))

    def test_once_refuses_to_compete_with_continuous_worker(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            lock_path = Path(temp_dir) / "job_worker.lock"
            with lock_path.open("a+") as active_worker_lock:
                fcntl.flock(
                    active_worker_lock.fileno(),
                    fcntl.LOCK_EX | fcntl.LOCK_NB,
                )
                with (
                    override_settings(LEGACY_MIGRATION_WORKER_LOCK_PATH=lock_path),
                    mock.patch(
                        "core.management.commands.process_legacy_migration_jobs."
                        "LegacyMigrationService"
                    ),
                    self.assertRaisesMessage(
                        CommandError,
                        "Another legacy migration worker is already running.",
                    ),
                ):
                    call_command("process_legacy_migration_jobs", "--once")


class GunicornLegacyMigrationWorkerTests(SimpleTestCase):
    def setUp(self):
        self.config = runpy.run_path(Path(settings.BASE_DIR) / "gunicorn.conf.py")
        self.server = SimpleNamespace(log=mock.Mock())

    @override_settings(LEGACY_MIGRATION_ENABLED=False)
    def test_when_ready_skips_worker_when_migration_is_disabled(self):
        with mock.patch.object(self.config["subprocess"], "Popen") as popen:
            self.config["when_ready"](self.server)

        popen.assert_not_called()
        self.server.log.info.assert_called_once_with(
            "Legacy migration is disabled; migration job worker will not start."
        )

    @override_settings(LEGACY_MIGRATION_ENABLED=True)
    def test_when_ready_starts_worker_and_on_exit_stops_it(self):
        process = mock.Mock(pid=4321)
        process.poll.return_value = None
        process.wait.return_value = 0
        with mock.patch.object(
            self.config["subprocess"], "Popen", return_value=process
        ) as popen:
            self.config["when_ready"](self.server)

        popen.assert_called_once_with(
            [
                self.config["sys"].executable,
                str(Path(settings.BASE_DIR) / "manage.py"),
                "process_legacy_migration_jobs",
            ],
            cwd=Path(settings.BASE_DIR),
            stdin=subprocess.DEVNULL,
            close_fds=True,
        )
        self.assertIs(self.server.legacy_migration_worker_process, process)

        self.config["on_exit"](self.server)

        process.terminate.assert_called_once_with()
        process.wait.assert_called_once_with(timeout=30)
        process.kill.assert_not_called()
        self.server.log.info.assert_any_call(
            "Legacy migration job worker pid %s stopped with code %s.",
            4321,
            0,
        )

    def test_on_exit_kills_worker_that_does_not_stop_gracefully(self):
        process = mock.Mock(pid=4321)
        process.poll.return_value = None
        process.wait.side_effect = [
            subprocess.TimeoutExpired("legacy migration worker", 30),
            -9,
        ]
        self.server.legacy_migration_worker_process = process

        self.config["on_exit"](self.server)

        process.terminate.assert_called_once_with()
        process.kill.assert_called_once_with()
        self.assertEqual(process.wait.call_count, 2)
        self.server.log.warning.assert_called_once()
