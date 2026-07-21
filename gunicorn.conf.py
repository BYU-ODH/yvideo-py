import os
from pathlib import Path
import subprocess
import sys

APP_ROOT = Path(__file__).resolve().parent
WORKER_ATTRIBUTE = "legacy_migration_worker_process"
WORKER_SHUTDOWN_TIMEOUT_SECONDS = 30


def when_ready(server):
    """Start one legacy migration queue worker from the Gunicorn master."""
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "yvideo.settings")
    from django.conf import settings

    if not settings.LEGACY_MIGRATION_ENABLED:
        server.log.info(
            "Legacy migration is disabled; migration job worker will not start."
        )
        return

    command = [
        sys.executable,
        str(APP_ROOT / "manage.py"),
        "process_legacy_migration_jobs",
    ]
    server.log.info("Starting legacy migration job worker: %s", " ".join(command))
    try:
        process = subprocess.Popen(
            command,
            cwd=APP_ROOT,
            stdin=subprocess.DEVNULL,
            close_fds=True,
        )
    except OSError:
        server.log.exception("Failed to start legacy migration job worker.")
        return

    setattr(server, WORKER_ATTRIBUTE, process)
    server.log.info(
        "Legacy migration job worker started with pid %s.",
        process.pid,
    )


def on_exit(server):
    """Stop the migration worker when the Gunicorn master exits."""
    process = getattr(server, WORKER_ATTRIBUTE, None)
    if process is None:
        server.log.info("No legacy migration job worker process to stop.")
        return

    return_code = process.poll()
    if return_code is not None:
        log = server.log.info if return_code == 0 else server.log.error
        log(
            "Legacy migration job worker pid %s already exited with code %s.",
            process.pid,
            return_code,
        )
        return

    server.log.info(
        "Stopping legacy migration job worker pid %s.",
        process.pid,
    )
    process.terminate()
    try:
        return_code = process.wait(timeout=WORKER_SHUTDOWN_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        server.log.warning(
            "Legacy migration job worker pid %s did not stop within %ss; killing it. "
            "Any running job will be recovered on the next worker startup.",
            process.pid,
            WORKER_SHUTDOWN_TIMEOUT_SECONDS,
        )
        process.kill()
        return_code = process.wait()

    server.log.info(
        "Legacy migration job worker pid %s stopped with code %s.",
        process.pid,
        return_code,
    )
