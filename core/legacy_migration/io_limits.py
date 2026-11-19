"""Keep legacy migration I/O from starving the live servers that share its disks.

`ionice` only takes effect under the BFQ (or old CFQ) I/O scheduler, so the
read limit below is the only control that works everywhere. See
LEGACY_MIGRATION.md for production guidance.
"""

import logging
import os
import shutil
import subprocess
import time

from django.conf import settings

logger = logging.getLogger(__name__)

CHUNK_SIZE = 1024 * 1024


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


def max_read_bytes_per_second():
    mbps = settings.LEGACY_MIGRATION_MAX_READ_MBPS or 0
    return int(mbps * 1_000_000)


def read_chunks(handle):
    limit = max_read_bytes_per_second()
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
    with open(source, "rb") as src, open(destination, "wb") as dst:
        for chunk in read_chunks(src):
            dst.write(chunk)
    shutil.copystat(source, destination)
