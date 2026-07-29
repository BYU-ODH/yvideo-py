import logging
from pathlib import Path
import shutil
import subprocess
import sys
from time import monotonic

from django.conf import settings

logger = logging.getLogger(__name__)


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


def run_legacy_dump():
    """Run scripts/dump_legacy_to_sqlite.py once, synchronously, and raise a
    RuntimeError with an admin-facing message if it could not be started or
    exited non-zero (for example: two dumps racing, or missing credentials).

    LegacyMigrationService calls this every time preflight needs the legacy
    catalog, so the local snapshot is never stale.
    """
    try:
        command = build_dump_command()
    except FileNotFoundError as exc:
        raise RuntimeError(str(exc)) from exc

    started_at = monotonic()
    logger.info("Running legacy dump subprocess: %s", " ".join(command))
    try:
        completed = subprocess.run(
            command,
            cwd=Path(__file__).resolve().parent.parent.parent,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        raise RuntimeError(f"Legacy dump subprocess failed to start: {exc}") from exc

    duration = monotonic() - started_at
    stdout = (completed.stdout or "").strip()
    stderr = (completed.stderr or "").strip()
    if completed.returncode != 0:
        raise RuntimeError(
            f"Legacy dump failed in {duration:.1f}s with exit code "
            f"{completed.returncode}. stdout={stdout} stderr={stderr}"
        )

    logger.info("Legacy dump completed in %.1fs. %s", duration, stdout)
    return duration
