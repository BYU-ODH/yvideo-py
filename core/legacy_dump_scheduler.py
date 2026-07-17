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

try:
    import fcntl
except ImportError:  # pragma: no cover - non-POSIX platforms
    fcntl = None

logger = logging.getLogger(__name__)

_scheduler_lock = Lock()
_scheduler_started = False
_scheduler_lock_file = None  # held open for the life of the process

# Long-running app servers that should host the scheduler. Anything else that
# happens to call django.setup() (one-off scripts, shells, cron jobs) must not.
SERVER_PROCESS_NAMES = {
    "daphne",
    "gunicorn",
    "hypercorn",
    "mod_wsgi-express",
    "uvicorn",
    "uwsgi",
}

SCHEDULER_PROCESS_ENV_VAR = "LEGACY_MIGRATION_AUTO_DUMP_PROCESS"


def _is_server_process():
    """Fail closed: only processes we can positively identify as a
    long-running app server may host the scheduler. Other servers (e.g.
    mod_wsgi embedded in Apache) opt in with LEGACY_MIGRATION_AUTO_DUMP_PROCESS=1.
    """
    if os.environ.get(SCHEDULER_PROCESS_ENV_VAR, "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }:
        return True

    argv = sys.argv
    argv0 = Path(argv[0]).name if argv and argv[0] else ""
    if argv0 == "manage.py":
        command = argv[1] if len(argv) > 1 else ""
        return command == "runserver" and os.environ.get("RUN_MAIN") == "true"
    return argv0 in SERVER_PROCESS_NAMES


def should_start_legacy_dump_scheduler():
    if not settings.LEGACY_MIGRATION_ENABLED:
        return False
    if not getattr(settings, "LEGACY_MIGRATION_AUTO_DUMP_ENABLED", True):
        return False
    if "pytest" in sys.modules or any("pytest" in arg for arg in sys.argv):
        return False
    return _is_server_process()


def acquire_scheduler_host_lock():
    """Hold an exclusive lock file for the life of this process so that at
    most one process per host runs the scheduler (e.g. one gunicorn worker).
    Returns the open lock file, or None if another process holds it."""
    if fcntl is None:
        return open(os.devnull, "w")

    sqlite_path = getattr(settings, "LEGACY_MIGRATION_SQLITE_PATH", None)
    if sqlite_path:
        lock_dir = Path(sqlite_path).parent
    else:
        lock_dir = Path(settings.BASE_DIR) / "var" / "legacy_migration"
    lock_dir.mkdir(parents=True, exist_ok=True)
    lock_file = open(lock_dir / "scheduler.lock", "w")
    try:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        lock_file.close()
        return None
    return lock_file


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
    global _scheduler_started, _scheduler_lock_file

    if not should_start_legacy_dump_scheduler():
        return

    with _scheduler_lock:
        if _scheduler_started:
            return
        host_lock = acquire_scheduler_host_lock()
        if host_lock is None:
            logger.info(
                "Legacy dump scheduler is already running in another process "
                "on this host; not starting a second one."
            )
            return
        _scheduler_lock_file = host_lock
        LegacyDumpScheduler().start()
        _scheduler_started = True
