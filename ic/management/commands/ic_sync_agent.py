"""The booth's long-running sync agent (IC_MODE_PLAN.md section 7.3).

It is the only part of the booth that talks to production, and it only ever calls out:
a cheap check every IC_CHECK_INTERVAL_SECONDS (`check_parent` decides whether a run is
due), and the queued runs themselves.
"""

import fcntl
import logging
from pathlib import Path
import signal
import time

from django.conf import settings
from django.core.management.base import BaseCommand
from django.core.management.base import CommandError
from django.utils import timezone

from ic import booth_state
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
        lock_path = Path(settings.IC_STATE_DIR) / "agent.lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        lock_file = open(lock_path, "w")  # noqa: SIM115 - held for the process lifetime
        try:
            fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise CommandError("Another ic_sync_agent is already running.") from exc

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
        # nothing changed, this costs one manifest request answered with a 304.
        if not ICSyncRun.objects.filter(status=ICSyncRun.Status.QUEUED).exists():
            ICSyncRun.objects.create(trigger=ICSyncRun.Trigger.AUTO)

        self.client = ParentClient()
        self.last_check = 0.0
        while not self.stopping:
            self.tick()
            queued = ICSyncRun.objects.filter(status=ICSyncRun.Status.QUEUED)
            # "Sync now" goes first: someone at the booth is watching its indicator.
            run = (
                queued.filter(trigger=ICSyncRun.Trigger.FORCE_PULL)
                .order_by("requested_at")
                .first()
                or queued.order_by("requested_at").first()
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
        booth_state.record_agent_heartbeat()
        if time.monotonic() - self.last_check >= settings.IC_CHECK_INTERVAL_SECONDS:
            self.last_check = time.monotonic()
            try:
                check_parent(self.client)
            except Exception:  # noqa: BLE001 - the agent loop must keep running
                logger.exception("Checking production failed")

    def _stop(self, signum, frame):
        self.stopping = True
