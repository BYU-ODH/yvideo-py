from datetime import timedelta
import logging
import os
from pathlib import Path
import shutil
import subprocess
import sys
from threading import Event
from threading import Lock
from threading import Thread
from time import monotonic

from django.conf import settings
from django.utils import timezone

logger = logging.getLogger(__name__)

_scheduler_lock = Lock()
_scheduler_started = False


def should_start_legacy_dump_scheduler():
    if not settings.LEGACY_MIGRATION_ENABLED:
        return False
    if not getattr(settings, "LEGACY_MIGRATION_AUTO_DUMP_ENABLED", True):
        return False
    if "pytest" in sys.modules or any("pytest" in arg for arg in sys.argv):
        return False

    argv = sys.argv
    if argv and Path(argv[0]).name == "manage.py":
        command = argv[1] if len(argv) > 1 else ""
        if command == "runserver":
            return os.environ.get("RUN_MAIN") == "true"
        return False

    return True


def seconds_until_next_run():
    now = timezone.localtime(timezone.now())
    target = now.replace(
        hour=int(getattr(settings, "LEGACY_MIGRATION_AUTO_DUMP_HOUR", 3)),
        minute=0,
        second=0,
        microsecond=0,
    )
    if now >= target:
        target += timedelta(days=1)
    return max((target - now).total_seconds(), 1.0)


def build_dump_command():
    script_path = Path(settings.LEGACY_MIGRATION_DUMP_SCRIPT)
    if not script_path.exists():
        raise FileNotFoundError(f"Legacy dump script was not found: {script_path}")
    uv_binary = shutil.which("uv")
    if uv_binary:
        return [uv_binary, "run", str(script_path)]
    logger.warning(
        "Could not find 'uv' on PATH. Falling back to the current Python interpreter."
    )
    return [sys.executable, str(script_path)]


class LegacyDumpScheduler(Thread):
    def __init__(self):
        super().__init__(name="legacy-dump-scheduler", daemon=True)
        self.stop_event = Event()

    def run(self):
        logger.info("Legacy dump scheduler started.")
        while not self.stop_event.is_set():
            if self.stop_event.wait(seconds_until_next_run()):
                break
            self.run_dump()

    def run_dump(self):
        command = build_dump_command()
        started_at = monotonic()
        logger.info("Running legacy dump subprocess: %s", " ".join(command))
        try:
            completed = subprocess.run(
                command,
                cwd=Path(__file__).resolve().parent.parent,
                capture_output=True,
                text=True,
                check=False,
            )
        except Exception:
            logger.exception("Legacy dump subprocess failed to start.")
            return

        duration = monotonic() - started_at
        if completed.returncode == 0:
            logger.info(
                "Legacy dump completed in %.1fs. %s",
                duration,
                (completed.stdout or "").strip(),
            )
            return

        logger.error(
            "Legacy dump failed in %.1fs with exit code %s. stdout=%s stderr=%s",
            duration,
            completed.returncode,
            (completed.stdout or "").strip(),
            (completed.stderr or "").strip(),
        )


def start_legacy_dump_scheduler():
    global _scheduler_started

    if not should_start_legacy_dump_scheduler():
        return

    with _scheduler_lock:
        if _scheduler_started:
            return
        LegacyDumpScheduler().start()
        _scheduler_started = True
