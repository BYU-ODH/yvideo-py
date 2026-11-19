"""Keep legacy migration I/O from starving other services that share its disks.

`ionice` only takes effect under the BFQ (or old CFQ) I/O scheduler, so the
read limit below is the only control that works everywhere. See
LEGACY_MIGRATION.md for production guidance.
"""

import logging
import os
from pathlib import Path
import shutil
import subprocess
import time

logger = logging.getLogger(__name__)

CHUNK_SIZE = 1024 * 1024
# 0 means unlimited. import_legacy_media sets this from --max-read-mbps.
max_read_mbps = 150


def lower_process_priority():
    try:
        os.nice(19)
    except OSError as exc:
        logger.warning("Could not lower CPU priority: %s", exc)
    try:
        subprocess.run(
            ["ionice", "-c3", "-p", str(os.getpid())],
            capture_output=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        logger.warning("Could not set idle I/O priority: %s", exc)


def read_chunks(handle):
    limit = int((max_read_mbps or 0) * 1_000_000)
    started = time.monotonic()
    total = 0
    for chunk in iter(lambda: handle.read(CHUNK_SIZE), b""):
        yield chunk
        if limit:
            total += len(chunk)
            delay = total / limit - (time.monotonic() - started)
            if delay > 0:
                time.sleep(delay)


def copy_file(source, destination):
    try:
        with open(source, "rb") as src, open(destination, "wb") as dst:
            for chunk in read_chunks(src):
                dst.write(chunk)
        shutil.copystat(source, destination)
    except BaseException:
        # A half-written copy must not be mistaken for real media later.
        Path(destination).unlink(missing_ok=True)
        raise
