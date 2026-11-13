import logging
import signal
import time

from django.core.management.base import BaseCommand

from ic import jobs

logger = logging.getLogger(__name__)

CLEANUP_INTERVAL_SECONDS = 6 * 3600
IDLE_SLEEP_SECONDS = 2


class Command(BaseCommand):
    help = "Run International Cinema media jobs (ingest, SD transcode, upload cleanup)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--once", action="store_true", help="Run due jobs, then exit."
        )

    def handle(self, *args, **options):
        self.stopping = False
        signal.signal(signal.SIGTERM, self._stop)
        signal.signal(signal.SIGINT, self._stop)
        recovered = jobs.recover_interrupted_jobs()
        if recovered:
            logger.warning(
                "Re-queued %s media job(s) interrupted by a restart.", recovered
            )

        last_cleanup = 0.0
        while not self.stopping:
            if time.monotonic() - last_cleanup > CLEANUP_INTERVAL_SECONDS:
                last_cleanup = time.monotonic()
                deleted = jobs.cleanup_uploads()
                if deleted:
                    logger.info("Deleted %s idle upload(s).", deleted)

            job = jobs.claim_next()
            if job is None:
                if options["once"]:
                    break
                time.sleep(IDLE_SLEEP_SECONDS)
                continue
            logger.info("Running media job %s", job)
            jobs.run(job)
            logger.info(
                "Media job %s finished: %s %s", job.pk, job.status, job.error or job.log
            )

    def _stop(self, signum, frame):
        self.stopping = True
