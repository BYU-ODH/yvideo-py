"""One booth sync run, whatever started it.

Every run follows this same pipeline: fetch the manifest, verify files, import and
verify the rows. The weekly run also re-hashes every file instead of trusting earlier
verification.

Sync is pull-only: `check_parent` is the agent's frequent check, and the one place that
decides when a run is due. Production can't start a run.
"""

from datetime import date
from datetime import timedelta
import logging
import os
from pathlib import Path
import shutil
import time

from django.conf import settings
from django.utils import timezone

from core.models import ResourceFile

from ..models import ICBoothState
from ..models import ICLocalFile
from ..models import ICSyncRun
from .bundle import file_xxh64
from .bundle import migration_leaves
from .client import MAX_BYTES_PER_SECOND
from .client import ParentClient
from .client import ParentError
from .importer import FingerprintMismatch
from .importer import apply_fixture

logger = logging.getLogger(__name__)

# A run that didn't end cleanly is retried this long after it finished, unless
# production's manifest has changed since (then at once).
RETRY_AFTER = timedelta(minutes=15)
# Released masters exist only on the booth; re-hash them weekly, in the morning, well
# away from showtimes.
WEEKLY_REHASH = timedelta(days=7)
REHASH_HOURS = range(3, 11)
# Headroom kept free on the booth disk beyond what a download needs.
DISK_HEADROOM_BYTES = 1024**3
# How long a local file production no longer lists is kept. Released masters exist
# nowhere else, so they are kept much longer, with a warning before they go.
FILE_PRUNE_GRACE = timedelta(days=3)
MASTER_PRUNE_AFTER = timedelta(days=30)
MASTER_PRUNE_WARNING = timedelta(days=7)


class RunFailed(Exception):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


def _storage_path(name):
    """Where a master lives locally: the same name it has on production."""
    return Path(ResourceFile().file.storage.path(name))


def _sort_key(entry):
    return entry.get("first_program_start") or "9999-12-31"


def disk_usage():
    try:
        usage = shutil.disk_usage(settings.IC_PROJECTION_ROOT)
    except OSError:
        return None
    return {"free": usage.free, "total": usage.total}


class RunContext:
    def __init__(self, run, client, heartbeat):
        self.run = run
        self.client = client
        self.heartbeat = heartbeat
        self._last_save = 0.0

    def save(self, force=False, **fields):
        """Save the run's progress (at most every second) and beat the heartbeat."""
        for key, value in fields.items():
            setattr(self.run, key, value)
        now = time.monotonic()
        if force or now - self._last_save > 1:
            self._last_save = now
            self.run.save()
            self.heartbeat()

    def add_bytes(self, count):
        self.run.bytes_done += count
        self.save()

    def verify(self, path, expected, *, count_bytes):
        """Hash a local file at the download rate cap, so verifying can't starve playback.

        Progress and the heartbeat keep moving meanwhile: a 10 GB master takes about
        ten minutes.
        """
        on_bytes = self.add_bytes if count_bytes else lambda _: self.save()
        digest = file_xxh64(
            path, max_bytes_per_second=MAX_BYTES_PER_SECOND, on_bytes=on_bytes
        )
        return digest == expected


def _check_migrations(manifest):
    """Refuse rows this booth's schema can't hold (or that production's can't make)."""
    remote = manifest.get("migrations", {})
    local = migration_leaves()
    if remote != local:
        raise RunFailed(
            "code_mismatch",
            f"Code version mismatch (production {remote}, booth {local}); "
            "wait for the deploy to finish.",
        )


def _plan_files(ctx, manifest, *, full_rehash):
    """Split the manifest's files into ones already verified here and ones to hash or fetch.

    Returns (results for the verified ones, [(entry, path, on_disk), ...] for the rest),
    soonest-needed first.
    """
    results = {}
    todo = []
    for entry in sorted(manifest.get("files", []), key=_sort_key):
        path = _storage_path(entry["name"])
        local = ICLocalFile.objects.filter(
            resource_file_id=entry["resource_file_id"]
        ).first()
        on_disk = path.exists() and path.stat().st_size == entry["size"]
        if local is not None:
            available = entry.get("available_from_parent", True)
            last_end = _date_or_none(entry.get("last_program_end"))
            if (local.available_from_parent, local.last_program_end) != (
                available,
                last_end,
            ):
                local.available_from_parent = available
                local.last_program_end = last_end
                local.save(update_fields=["available_from_parent", "last_program_end"])
        if (
            local is not None
            and local.xxh64 == entry["xxh64"]
            and on_disk
            and not full_rehash
        ):
            results[entry["resource_file_id"]] = {"ok": True}
        else:
            todo.append((entry, path, on_disk))
    ctx.save(force=True, bytes_total=sum(e["size"] for e, _, _ in todo), bytes_done=0)
    return results, todo


def _room_for(entry, part):
    """Whether the disk can hold the rest of this download, plus some headroom.

    Checked per file, so a film that doesn't fit only fails itself: tonight's rows and
    smaller files still arrive.
    """
    part.parent.mkdir(parents=True, exist_ok=True)
    have = part.stat().st_size if part.exists() else 0
    free = shutil.disk_usage(part.parent).free
    return free >= entry["size"] - have + DISK_HEADROOM_BYTES


def _date_or_none(value):
    return date.fromisoformat(value) if value else None


def _fetch_file(ctx, entry, path, on_disk):
    """Hash, or download and verify, one planned file. Returns its result."""
    if on_disk:
        if ctx.verify(path, entry["xxh64"], count_bytes=True):
            return {"ok": True}
        # These bytes are damaged: they must never count as verified again, even if
        # they can't be replaced now.
        ICLocalFile.objects.filter(resource_file_id=entry["resource_file_id"]).delete()
    if not entry.get("available_from_parent", True):
        return {
            "ok": False,
            "lost": True,
            "error": f"{entry['name']} is missing or damaged here and production released its copy; re-upload the master.",
        }
    part = path.with_name(path.name + ".part")
    if not _room_for(entry, part):
        return {
            "ok": False,
            "error": f"Not enough disk space on the booth for {entry['name']}.",
        }
    try:
        ctx.client.download(entry, part, on_bytes=ctx.add_bytes)
    except ParentError as exc:
        return {"ok": False, "error": f"Couldn't download {entry['name']}: {exc}"}
    if not ctx.verify(part, entry["xxh64"], count_bytes=False):
        part.unlink(missing_ok=True)
        return {
            "ok": False,
            "error": f"Checksum failed for {entry['name']}; the next sync will download it again.",
        }
    path.parent.mkdir(parents=True, exist_ok=True)
    os.replace(part, path)
    return {"ok": True}


def _fetch_files(ctx, todo):
    results = {}
    for entry, path, on_disk in todo:
        result = _fetch_file(ctx, entry, path, on_disk)
        result["name"] = entry["name"]
        if result["ok"]:
            ICLocalFile.objects.update_or_create(
                resource_file_id=entry["resource_file_id"],
                defaults={
                    "name": entry["name"],
                    "size": entry["size"],
                    "xxh64": entry["xxh64"],
                    "verified_at": timezone.now(),
                    "available_from_parent": entry.get("available_from_parent", True),
                    "last_program_end": _date_or_none(entry.get("last_program_end")),
                },
            )
        results[entry["resource_file_id"]] = result
    return results


def _showing_master(entry, today):
    """A master for a program that has already started: rows wait for it."""
    start = entry.get("first_program_start")
    return bool(start) and date.fromisoformat(start) <= today


def _unfetched(planned, file_results):
    """Names of planned files that failed but production could still send."""
    return [
        entry["name"]
        for entry, _, _ in planned
        if not file_results[entry["resource_file_id"]]["ok"]
        and entry.get("available_from_parent", True)
    ]


def _apply_database(ctx, manifest, manifest_digest):
    """Replace the mirror with production's rows, verified by fingerprint.

    This never touches the film that's playing: the output holds its item's player data
    in memory and streams from a URL naming one verified file (`ic_local_file`), whose
    bytes no import can change. New data takes effect when the next item loads.
    """
    ctx.save(force=True, phase="Importing")
    try:
        apply_fixture(
            manifest["fixture"],
            manifest.get("fingerprints", {}),
            manifest.get("subtitles", {}),
        )
    except FingerprintMismatch as exc:
        raise RunFailed("fingerprint_mismatch", str(exc)) from exc
    state = ICBoothState.get()
    state.applied_manifest_hash = manifest_digest
    state.applied_at = timezone.now()
    state.save(update_fields=["applied_manifest_hash", "applied_at"])


def prune(manifest, today=None):
    """Delete local files production no longer lists, with longer grace for released masters."""
    today = today or timezone.localdate()
    listed = {e["resource_file_id"] for e in manifest.get("files", [])}
    removed, warnings = [], []
    for local in ICLocalFile.objects.all():
        if local.resource_file_id in listed:
            if not local.available_from_parent and local.last_program_end:
                delete_on = local.last_program_end + MASTER_PRUNE_AFTER
                if delete_on - today <= MASTER_PRUNE_WARNING:
                    warnings.append(
                        f"The booth will delete its only copy of {local.name} on {delete_on}; "
                        "re-upload will be needed to show it again."
                    )
            continue
        grace = FILE_PRUNE_GRACE if local.available_from_parent else MASTER_PRUNE_AFTER
        reference = local.last_program_end or local.verified_at.date()
        if today - reference < grace:
            continue
        _storage_path(local.name).unlink(missing_ok=True)
        removed.append(local.name)
        local.delete()
    return removed, warnings


def execute_run(run, client=None, heartbeat=None):
    client = client or ParentClient()
    ctx = RunContext(run, client, heartbeat or ICBoothState.record_agent_heartbeat)
    ctx.save(
        force=True,
        status=ICSyncRun.Status.RUNNING,
        started_at=timezone.now(),
        phase="Contacting production",
    )
    report = {"run_id": str(run.pk)}
    try:
        manifest = client.manifest()
        digest = manifest.get("manifest_hash", "")
        ctx.save(force=True, manifest_hash=digest)
        _check_migrations(manifest)
        ctx.save(force=True, phase="Downloading")
        file_results, todo = _plan_files(ctx, manifest, full_rehash=run.full_rehash)
        # Masters for programs already showing come first, then the rows (with their
        # subtitles), then masters for later programs: a fix to tonight's annotations
        # must not wait behind next month's 20 GB master.
        today = timezone.localdate()
        showing = [t for t in todo if _showing_master(t[0], today)]
        upcoming = [t for t in todo if t not in showing]
        file_results.update(_fetch_files(ctx, showing))
        # Swapping in rows that point at a master the booth couldn't fetch would make a
        # film that plays today unplayable. Keep the previous rows until a later run
        # fetches it, unless production can't send it anyway (a lost master).
        blocking = _unfetched(showing, file_results)
        warnings = []
        if blocking:
            database = "kept_previous"
            warnings.append(
                "The booth kept its previous data because these files aren't "
                "verified yet: " + ", ".join(blocking) + ". The next sync will "
                "try again."
            )
        else:
            _apply_database(ctx, manifest, digest)
            database = "applied"
        if upcoming:
            ctx.save(force=True, phase="Downloading upcoming films")
            file_results.update(_fetch_files(ctx, upcoming))
        removed, prune_warnings = prune(manifest)
        failures = [r for r in file_results.values() if not r["ok"]]
        report.update(
            {
                "database": database,
                "failed_files": [
                    {"name": r["name"], "error": r["error"]} for r in failures
                ],
                "pruned": removed,
                "warnings": warnings + prune_warnings,
            }
        )
        # Partial means a later run could do better: rows kept, or a file production
        # can still send didn't arrive. A lost master is reported, but only a
        # re-upload (which changes the manifest) can fix it, so it doesn't count.
        retryable = database != "applied" or any(not r.get("lost") for r in failures)
        run.status = (
            ICSyncRun.Status.PARTIAL if retryable else ICSyncRun.Status.SUCCEEDED
        )
    except RunFailed as exc:
        report["code"] = exc.code
        run.status, run.error = ICSyncRun.Status.FAILED, str(exc)
    except ParentError as exc:
        report["code"] = "unreachable"
        run.status, run.error = ICSyncRun.Status.FAILED, str(exc)
    except Exception as exc:  # noqa: BLE001 - a run must always finish with a status
        logger.exception("Sync run %s failed", run.pk)
        report["code"] = "error"
        run.status, run.error = ICSyncRun.Status.FAILED, f"Unexpected error: {exc}"

    report.update(
        {
            "status": run.status,
            "error": run.error,
            "applied_manifest_hash": ICBoothState.get().applied_manifest_hash,
            "verified_files": [
                {"resource_file_id": local.resource_file_id, "xxh64": local.xxh64}
                for local in ICLocalFile.objects.all()
            ],
            "disk": disk_usage(),
        }
    )
    ctx.save(force=True, report=report, finished_at=timezone.now(), phase="")
    try:
        client.post_status(report)
    except ParentError as exc:
        run.report = {**report, "status_report_error": str(exc)}
        run.save(update_fields=["report"])
    return run


def _rehash_due(now):
    """The weekly re-hash of the only copies of released masters."""
    last = ICSyncRun.latest_finished(
        full_rehash=True,
        status__in=[ICSyncRun.Status.SUCCEEDED, ICSyncRun.Status.PARTIAL],
    )
    return (
        timezone.localtime(now).hour in REHASH_HOURS
        and (last is None or now - last.finished_at >= WEEKLY_REHASH)
        and ICLocalFile.objects.filter(available_from_parent=False).exists()
    )


def check_parent(client=None, now=None):
    """The agent's frequent, cheap check (every CHECK_INTERVAL_SECONDS).

    Asks production for its manifest hash and queues a run when one is due:

    - production has changes the booth hasn't tried yet: at once;
    - the booth is behind or the last run didn't end cleanly: RETRY_AFTER later;
    - otherwise, the weekly re-hash of released masters, in the morning.

    Returns the queued run, or None.
    """
    client = client or ParentClient()
    now = now or timezone.now()
    state = ICBoothState.get()
    try:
        parent_hash = str(client.check().get("manifest_hash") or "")[:64]
    except ParentError as exc:
        state.parent_check_error = str(exc)
        state.save(update_fields=["parent_check_error"])
        return None
    state.parent_manifest_hash = parent_hash
    state.parent_checked_at = now
    state.parent_check_error = ""
    state.save(
        update_fields=[
            "parent_manifest_hash",
            "parent_checked_at",
            "parent_check_error",
        ]
    )

    if ICSyncRun.objects.filter(
        status__in=[ICSyncRun.Status.QUEUED, ICSyncRun.Status.RUNNING]
    ).exists():
        return None
    last = ICSyncRun.latest_finished()
    behind = parent_hash != state.applied_manifest_hash or (
        last is not None and last.status != ICSyncRun.Status.SUCCEEDED
    )
    if behind and (
        last is None
        # A run that failed before it saw a manifest waits like any other retry, so a
        # broken production endpoint isn't asked again every check.
        or (last.manifest_hash and parent_hash != last.manifest_hash)
        or now - last.finished_at >= RETRY_AFTER
    ):
        return ICSyncRun.objects.create()
    if _rehash_due(now):
        return ICSyncRun.objects.create(full_rehash=True)
    return None
