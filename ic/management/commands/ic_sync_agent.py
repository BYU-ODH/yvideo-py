"""The booth's long-running sync agent.

It is the only part of the booth that talks to production, and it only ever calls out:
a cheap check every CHECK_INTERVAL_SECONDS (`check_parent` decides whether a run is
due), and the queued runs themselves.
"""

import logging
import signal
import time

from django.conf import settings
from django.core.management.base import BaseCommand
from django.core.management.base import CommandError
from django.utils import timezone

from ic.models import CHECK_INTERVAL_SECONDS
from ic.models import ICBoothState
from ic.models import ICSyncRun
from ic.sync.agent import check_parent
from ic.sync.agent import execute_run
from ic.sync.client import ParentClient

logger = logging.getLogger(__name__)

QUEUE_POLL_SECONDS = 2


class Command(BaseCommand):
    help = "Run the International Cinema booth sync agent."

    def add_arguments(self, parser):
        parser.add_argument(
            "--once",
            action="store_true",
            help="Check production once, run anything queued, then exit.",
        )

    def handle(self, *args, **options):
        if not settings.IS_IC_INSTANCE:
            raise CommandError(
                "ic_sync_agent runs only on an IC instance (IS_IC_INSTANCE = True)."
            )
        self.stopping = False
        signal.signal(signal.SIGTERM, self._stop)
        signal.signal(signal.SIGINT, self._stop)

        stale = ICSyncRun.objects.filter(status=ICSyncRun.Status.RUNNING).update(
            status=ICSyncRun.Status.FAILED,
            error="The sync agent restarted during this run.",
            finished_at=timezone.now(),
        )
        if stale:
            logger.warning("Marked %s interrupted sync run(s) failed.", stale)
        # Sync once at startup: the agent usually restarts because the booth just
        # deployed, which is when a run refused for a code mismatch can succeed. When
        # nothing changed, the run only re-reads the manifest.
        if not ICSyncRun.objects.filter(status=ICSyncRun.Status.QUEUED).exists():
            ICSyncRun.objects.create(trigger=ICSyncRun.Trigger.AUTO)

        self.client = ParentClient()
        self.last_check = None  # check at once (time.monotonic() counts from boot)
        while not self.stopping:
            self.tick()
            run = (
                ICSyncRun.objects.filter(status=ICSyncRun.Status.QUEUED)
                .order_by("requested_at")
                .first()
            )
            if run is not None:
                logger.info("Starting sync run %s (%s)", run.pk, run.trigger)
                execute_run(run, client=self.client, heartbeat=self.tick)
                logger.info(
                    "Sync run %s finished: %s %s", run.pk, run.status, run.error
                )
                continue
            if options.get("once"):
                break
            time.sleep(QUEUE_POLL_SECONDS)

    def tick(self):
        """Heartbeat, and the check for changes when it's due.

        Also called throughout a run, which keeps production's "last contact" fresh
        during a long download; check_parent queues nothing while a run is active.
        """
        ICBoothState.record_agent_heartbeat()
        if (
            self.last_check is None
            or time.monotonic() - self.last_check >= CHECK_INTERVAL_SECONDS
        ):
            self.last_check = time.monotonic()
            try:
                check_parent(self.client)
            except Exception:  # noqa: BLE001 - the agent loop must keep running
                logger.exception("Checking production failed")

    def _stop(self, signum, frame):
        self.stopping = True
