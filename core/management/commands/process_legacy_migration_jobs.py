import fcntl
import logging
import os
from pathlib import Path
import signal
import time

from django.conf import settings
from django.core.management.base import BaseCommand
from django.core.management.base import CommandError

from core.legacy_migration import LegacyMigrationService

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Process queued legacy migration preflight/import jobs."

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.shutdown_requested = False

    def add_arguments(self, parser):
        parser.add_argument(
            "--once",
            action="store_true",
            help="Process at most one queued job and then exit.",
        )
        parser.add_argument(
            "--sleep-seconds",
            type=int,
            default=5,
            help="Poll interval when running in loop mode.",
        )

    def _handle_shutdown_signal(self, signum, _frame):
        if not self.shutdown_requested:
            logger.warning(
                "Legacy migration worker received %s; stopping after the current "
                "job finishes.",
                signal.Signals(signum).name,
            )
        self.shutdown_requested = True

    def _acquire_worker_lock(self, once):
        lock_path = Path(settings.LEGACY_MIGRATION_WORKER_LOCK_PATH)
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        lock_file = lock_path.open("a+")
        logger.info("Waiting for legacy migration worker lock at %s.", lock_path)
        lock_flags = fcntl.LOCK_EX | (fcntl.LOCK_NB if once else 0)
        try:
            fcntl.flock(lock_file.fileno(), lock_flags)
        except BlockingIOError as exc:
            lock_file.close()
            raise CommandError(
                "Another legacy migration worker is already running."
            ) from exc
        logger.info(
            "Acquired legacy migration worker lock at %s; pid=%s.",
            lock_path,
            os.getpid(),
        )
        return lock_file

    def handle(self, *args, **options):
        service = LegacyMigrationService(require_catalog=False)
        once = options["once"]
        sleep_seconds = max(options["sleep_seconds"], 1)
        lock_file = self._acquire_worker_lock(once)
        previous_sigterm_handler = signal.signal(
            signal.SIGTERM, self._handle_shutdown_signal
        )
        previous_sigint_handler = signal.signal(
            signal.SIGINT, self._handle_shutdown_signal
        )
        logger.info(
            "Legacy migration worker started; pid=%s, once=%s, poll_interval=%ss.",
            os.getpid(),
            once,
            sleep_seconds,
        )

        try:
            recovered_count = service.recover_running_jobs()
            logger.info(
                "Legacy migration worker startup recovery finished; recovered=%s.",
                recovered_count,
            )

            while not self.shutdown_requested:
                try:
                    job = service.run_next_job()
                except Exception as exc:
                    if once:
                        raise CommandError(str(exc)) from exc
                    # The failed job is already marked failed; keep the worker
                    # alive so queued jobs behind it still get processed.
                    logger.exception("Legacy migration job failed; continuing.")
                    self.stderr.write(self.style.ERROR(f"Job failed: {exc}"))
                    time.sleep(sleep_seconds)
                    continue

                if once:
                    if job:
                        self.stdout.write(
                            self.style.SUCCESS(
                                f"Processed job {job.pk} ({job.job_type})."
                            )
                        )
                    else:
                        self.stdout.write("No queued legacy migration jobs found.")
                    return

                if not job:
                    time.sleep(sleep_seconds)
        finally:
            signal.signal(signal.SIGTERM, previous_sigterm_handler)
            signal.signal(signal.SIGINT, previous_sigint_handler)
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
            lock_file.close()
            logger.info("Legacy migration worker stopped; pid=%s.", os.getpid())
