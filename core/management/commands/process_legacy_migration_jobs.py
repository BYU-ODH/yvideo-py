import time

from django.core.management.base import BaseCommand
from django.core.management.base import CommandError

from core.legacy_migration import LegacyMigrationService


class Command(BaseCommand):
    help = "Process queued legacy migration preflight/import jobs."

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

    def handle(self, *args, **options):
        service = LegacyMigrationService(require_catalog=False)
        once = options["once"]
        sleep_seconds = max(options["sleep_seconds"], 1)

        while True:
            try:
                job = service.run_next_job()
            except Exception as exc:
                raise CommandError(str(exc)) from exc

            if once:
                if job:
                    self.stdout.write(
                        self.style.SUCCESS(f"Processed job {job.pk} ({job.job_type}).")
                    )
                else:
                    self.stdout.write("No queued legacy migration jobs found.")
                return

            if not job:
                time.sleep(sleep_seconds)
