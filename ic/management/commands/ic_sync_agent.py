"""The booth's long-running sync agent (IC_MODE_PLAN.md section 7.3)."""

from datetime import timedelta
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
from ic.models import ICBoothState
from ic.models import ICLocalFile
from ic.models import ICSyncRun
from ic.sync.agent import execute_run

logger = logging.getLogger(__name__)

QUEUE_POLL_SECONDS = 2
WEEKLY_REHASH = timedelta(days=7)
# Re-hashing the only copies of released masters reads tens of GB; do it in the morning,
# well away from evening showtimes.
REHASH_HOURS = range(3, 11)


class Command(BaseCommand):
    help = "Run the International Cinema booth sync agent."

    def add_arguments(self, parser):
        parser.add_argument(
            "--once",
            action="store_true",
            help="Run one poll (and any queued runs), then exit.",
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

        stale = ICSyncRun.objects.filter(
            status__in=[ICSyncRun.Status.RUNNING, ICSyncRun.Status.WAITING_FOR_PLAYBACK]
        ).update(
            status=ICSyncRun.Status.FAILED,
            error="The sync agent restarted during this run.",
            finished_at=timezone.now(),
        )
        if stale:
            logger.warning("Marked %s interrupted sync run(s) failed.", stale)

        last_poll = 0.0
        while not self.stopping:
            booth_state.record_agent_heartbeat()
            queued = ICSyncRun.objects.filter(status=ICSyncRun.Status.QUEUED).order_by(
                "requested_at"
            )
            # Forced runs go first: someone is watching their indicator.
            run = (
                queued.filter(
                    trigger__in=[
                        ICSyncRun.Trigger.FORCE_PULL,
                        ICSyncRun.Trigger.FORCE_PUSH,
                    ]
                ).first()
                or queued.first()
            )
            if (
                run is None
                and time.monotonic() - last_poll >= settings.IC_POLL_INTERVAL_SECONDS
            ):
                run_options = {}
                last_rehash = ICBoothState.get().last_full_rehash_at
                if (
                    (
                        last_rehash is None
                        or timezone.now() - last_rehash >= WEEKLY_REHASH
                    )
                    and timezone.localtime().hour in REHASH_HOURS
                    and not booth_state.playback_active()
                    and ICLocalFile.objects.filter(available_from_parent=False).exists()
                ):
                    run_options["full_rehash"] = True
                run = ICSyncRun.objects.create(
                    trigger=ICSyncRun.Trigger.POLL, options=run_options
                )
            if run is not None:
                if run.trigger == ICSyncRun.Trigger.POLL:
                    last_poll = time.monotonic()
                logger.info("Starting sync run %s (%s)", run.pk, run.trigger)
                execute_run(run)
                logger.info(
                    "Sync run %s finished: %s %s", run.pk, run.status, run.error
                )
                continue
            if options.get("once"):
                break
            time.sleep(QUEUE_POLL_SECONDS)

    def _stop(self, signum, frame):
        self.stopping = True
