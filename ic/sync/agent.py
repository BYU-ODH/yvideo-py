"""One booth sync run, whatever started it (IC_MODE_PLAN.md sections 7.3-7.4, 7.8).

Every trigger runs this same pipeline. Forcing changes when it runs and how thoroughly
it checks (always re-import and re-verify, optionally re-hash every file), never whether
it verifies.

Sync is pull-only: `check_parent` is the agent's frequent check, and the one place that
decides when a run is due. Production can't start a run.
"""

from datetime import date
from datetime import timedelta
import hashlib
import logging
import os
from pathlib import Path
import shutil
import time

from django.conf import settings
from django.utils import timezone

from core.models import ResourceFile
from core.models import Subtitle

from .. import booth_state
from ..models import ICBoothState
from ..models import ICLocalFile
from ..models import ICSyncRun
from .bundle import file_xxh64
from .bundle import git_sha
from .bundle import migration_leaves
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


class RunFailed(Exception):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


def _storage_path(entry):
    """Where a manifest file lives locally: the same name it has on production."""
    if entry["kind"] == "projection":
        return Path(ResourceFile().file.storage.path(entry["name"]))
    return Path(Subtitle().subtitles_file.storage.path(entry["name"]))


def _ref_id(entry):
    return (
        entry["resource_file_id"]
        if entry["kind"] == "projection"
        else entry["subtitle_id"]
    )


def _sort_key(entry):
    return (
        entry.get("first_program_start") or "9999-12-31",
        entry["kind"] != "subtitle",
    )


class RunContext:
    def __init__(self, run, client, heartbeat):
        self.run = run
        self.client = client
        self.heartbeat = heartbeat
        self._last_save = 0.0

    def save(self, force=False, **fields):
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


def _check_code_version(manifest):
    local_sha = git_sha()
    local_migrations = migration_leaves()
    remote_migrations = manifest.get("migrations", {})
    if remote_migrations != local_migrations:
        behind = []
        for app in sorted(set(remote_migrations) | set(local_migrations)):
            remote, local = (
                remote_migrations.get(app, ""),
                local_migrations.get(app, ""),
            )
            if remote != local:
                side = "booth" if remote > local else "production"
                behind.append(
                    f"{app}: production {remote or '-'}, booth {local or '-'} ({side} is behind)"
                )
        raise RunFailed(
            "code_mismatch",
            "Code version mismatch; "
            + "; ".join(behind)
            + ". Wait for the deploy to finish.",
        )
    remote_sha = manifest.get("git_sha", "")
    if remote_sha and local_sha not in ("unknown", "") and remote_sha != local_sha:
        raise RunFailed(
            "code_mismatch",
            f"Code version mismatch: production is at {remote_sha[:10]}, the booth at "
            f"{local_sha[:10]}. The booth updates its code outside playback hours.",
        )


def _plan_files(ctx, manifest, *, full_rehash):
    """Split the manifest's files into ones already verified here and ones to hash or fetch.

    Returns (results for the verified ones, [(entry, path, on_disk), ...] for the rest),
    soonest-needed first, and refuses the run if the disk can't hold the downloads.
    """
    entries = sorted(manifest.get("files", []), key=_sort_key)
    results = {}
    todo = []
    for entry in entries:
        path = _storage_path(entry)
        local = ICLocalFile.objects.filter(
            kind=entry["kind"], ref_id=_ref_id(entry)
        ).first()
        on_disk = path.exists() and path.stat().st_size == entry["size"]
        trusted = (
            local is not None
            and local.xxh64 == entry["xxh64"]
            and on_disk
            and not full_rehash
        )
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
        if trusted:
            results[(entry["kind"], _ref_id(entry))] = {
                "ok": True,
                "hashed_this_run": False,
            }
        else:
            todo.append((entry, path, on_disk))

    needed = sum(entry["size"] for entry, _, on_disk in todo if not on_disk)
    ctx.save(
        force=True,
        files_total=len(entries),
        files_done=len(entries) - len(todo),
        bytes_total=sum(entry["size"] for entry, _, _ in todo),
        bytes_done=0,
    )
    if needed:
        root = Path(settings.IC_PROJECTION_ROOT)
        root.mkdir(parents=True, exist_ok=True)
        free = shutil.disk_usage(root).free
        if free < needed + DISK_HEADROOM_BYTES:
            raise RunFailed(
                "disk_full",
                f"Not enough disk space on the booth: {free / 1e9:.1f} GB free, "
                f"{(needed + DISK_HEADROOM_BYTES) / 1e9:.1f} GB needed.",
            )
    return results, todo


def _date_or_none(value):
    return date.fromisoformat(value) if value else None


def _fetch_files(ctx, todo):
    """Hash, or download and verify, each planned file. Returns per-file results."""
    results = {}
    for entry, path, on_disk in todo:
        key = (entry["kind"], _ref_id(entry))
        ok = False
        if on_disk:
            ok = file_xxh64(path) == entry["xxh64"]
            ctx.add_bytes(entry["size"])
            if not ok:
                # These bytes are damaged: they must never count as verified again, even
                # if they can't be replaced now.
                ICLocalFile.objects.filter(
                    kind=entry["kind"], ref_id=_ref_id(entry)
                ).delete()
        if not ok:
            if not entry.get("available_from_parent", True):
                results[key] = {
                    "ok": False,
                    "lost": True,
                    "error": f"{entry['name']} is missing or damaged here and production released its copy; re-upload the master.",
                }
                ctx.save(files_done=ctx.run.files_done + 1)
                continue
            part = path.with_name(path.name + ".part")
            for attempt in range(2):
                try:
                    ctx.client.download(entry, part, on_bytes=ctx.add_bytes)
                except ParentError as exc:
                    results[key] = {"ok": False, "error": str(exc)}
                    break
                if file_xxh64(part) == entry["xxh64"]:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    os.replace(part, path)
                    ok = True
                    break
                part.unlink(missing_ok=True)
                if attempt == 1:
                    results[key] = {
                        "ok": False,
                        "error": f"Checksum failed for {entry['name']} twice; the booth kept its previous data.",
                        "code": "checksum_failed",
                    }
        if ok:
            ICLocalFile.objects.update_or_create(
                kind=entry["kind"],
                ref_id=_ref_id(entry),
                defaults={
                    "name": entry["name"],
                    "size": entry["size"],
                    "xxh64": entry["xxh64"],
                    "verified_at": timezone.now(),
                    "available_from_parent": entry.get("available_from_parent", True),
                    "last_program_end": _date_or_none(entry.get("last_program_end")),
                },
            )
            results[key] = {"ok": True, "hashed_this_run": True}
        ctx.save(files_done=ctx.run.files_done + 1)
    return results


def _needed_before_rows(entry, today):
    """Whether a file must be verified before production's rows are applied.

    Subtitle files always are: the player JSON embeds their text, so the fingerprints
    can't match without them (and they are small). Masters are needed first only for
    programs that have already started; masters for later programs come afterwards.
    """
    if entry["kind"] != "projection":
        return True
    start = entry.get("first_program_start")
    return bool(start) and date.fromisoformat(start) <= today


def _program_results(manifest, file_results):
    by_resource = {}
    for entry in manifest.get("files", []):
        by_resource.setdefault(entry.get("resource_id"), []).append(entry)
    programs = []
    for program in manifest.get("programs", []):
        problems = list(program.get("production_problems", []))
        files_ready = True
        for resource_id in program.get("resource_ids", []):
            for entry in by_resource.get(resource_id, []):
                result = file_results.get((entry["kind"], _ref_id(entry)), {})
                if not result.get("ok"):
                    files_ready = False
                    problems.append(
                        result.get("error")
                        or f"{entry['name']} isn't on the booth yet."
                    )
        programs.append(
            {
                "id": program["id"],
                "name": program["name"],
                "files_ready": files_ready,
                "ready": files_ready and not program.get("production_problems"),
                "problems": problems,
            }
        )
    return programs


def _apply_database(ctx, manifest, manifest_digest):
    """Replace the mirror with production's rows, verified by fingerprint.

    This never touches the film that's playing: the output holds its item's player data
    in memory and streams from a URL naming one verified file (`ic_local_file`), whose
    bytes no import can change. New data takes effect when the next item loads.
    """
    state = ICBoothState.get()
    fixture_sha = manifest["fixture"]["sha256"]
    if ctx.run.is_forced or fixture_sha != state.applied_fixture_sha256:
        ctx.save(force=True, phase="importing")
        fixture_bytes = ctx.client.fixture()
        if hashlib.sha256(fixture_bytes).hexdigest() != fixture_sha:
            raise RunFailed(
                "changed_during_sync",
                "Production changed while the booth was syncing; the next sync will pick it up.",
            )
        ctx.save(force=True, phase="verifying")
        try:
            apply_fixture(fixture_bytes, manifest.get("fingerprints", {}))
        except FingerprintMismatch as exc:
            raise RunFailed("fingerprint_mismatch", str(exc)) from exc
        outcome = "applied"
    else:
        outcome = "unchanged"
    # Even when the rows are unchanged (only files moved), this manifest is now the one
    # the booth matches.
    state.applied_manifest_hash = manifest_digest
    state.applied_fixture_sha256 = fixture_sha
    state.applied_manifest = manifest
    state.applied_at = timezone.now()
    state.save(
        update_fields=[
            "applied_manifest_hash",
            "applied_fixture_sha256",
            "applied_manifest",
            "applied_at",
        ]
    )
    return outcome


def prune(manifest, today=None):
    """Delete local files production no longer lists, with longer grace for released masters."""
    today = today or timezone.localdate()
    listed = {(e["kind"], _ref_id(e)) for e in manifest.get("files", [])}
    removed, warnings = [], []
    for local in ICLocalFile.objects.all():
        if (local.kind, local.ref_id) in listed:
            if not local.available_from_parent and local.last_program_end:
                delete_on = local.last_program_end + timedelta(
                    days=settings.IC_MASTER_PRUNE_DAYS
                )
                if delete_on - today <= timedelta(
                    days=settings.IC_MASTER_PRUNE_WARNING_DAYS
                ):
                    warnings.append(
                        f"The booth will delete its only copy of {local.name} on {delete_on}; "
                        "re-upload will be needed to show it again."
                    )
            continue
        if local.available_from_parent:
            grace = timedelta(days=settings.IC_FILE_PRUNE_GRACE_DAYS)
        else:
            grace = timedelta(days=settings.IC_MASTER_PRUNE_DAYS)
        reference = local.last_program_end or local.verified_at.date()
        if today - reference < grace:
            continue
        path = _storage_path({"kind": local.kind, "name": local.name})
        path.unlink(missing_ok=True)
        removed.append(local.name)
        local.delete()
    return removed, warnings


def _if_none_match(run, state):
    """The manifest hash to send with If-None-Match, or None to always fetch.

    Forced runs always fetch. So does any run after one that didn't finish cleanly, so
    files a partial run couldn't download are retried by the next poll.
    """
    if run.is_forced or run.options.get("full_rehash"):
        return None
    if state.last_run_status not in (
        ICSyncRun.Status.SUCCEEDED,
        ICSyncRun.Status.UP_TO_DATE,
    ):
        return None
    return state.applied_manifest_hash or None


def execute_run(run, client=None, heartbeat=None):
    client = client or ParentClient()
    heartbeat = heartbeat or (lambda: booth_state.record_agent_heartbeat())
    ctx = RunContext(run, client, heartbeat)
    ctx.save(
        force=True,
        status=ICSyncRun.Status.RUNNING,
        started_at=timezone.now(),
        phase="contacting",
    )
    state = ICBoothState.get()
    report = {"run_id": str(run.pk), "trigger": run.trigger}
    file_results = {}
    try:
        manifest, digest = client.manifest(if_none_match=_if_none_match(run, state))
        if manifest is None:
            ctx.save(
                force=True,
                status=ICSyncRun.Status.UP_TO_DATE,
                manifest_hash=digest or "",
            )
        else:
            ctx.save(force=True, manifest_hash=digest)
            _check_code_version(manifest)
            ctx.save(force=True, phase="downloading")
            file_results, todo = _plan_files(
                ctx, manifest, full_rehash=run.options.get("full_rehash", False)
            )
            # Masters for programs already showing (and all subtitles) come first, then
            # the rows, then masters for later programs: a fix to tonight's annotations
            # must not wait behind next month's 20 GB master.
            today = timezone.localdate()
            first = [t for t in todo if _needed_before_rows(t[0], today)]
            upcoming = [t for t in todo if not _needed_before_rows(t[0], today)]
            file_results.update(_fetch_files(ctx, first))
            # Swapping in rows that point at a file the booth couldn't fetch would make
            # a film that plays today unplayable. Keep the previous rows until a later
            # run fetches it, unless production can't send it anyway (a lost master).
            blocking = [
                entry["name"]
                for entry, _, _ in first
                if not file_results[(entry["kind"], _ref_id(entry))].get("ok")
                and entry.get("available_from_parent", True)
            ]
            warnings = []
            if blocking:
                database = "kept_previous"
                warnings.append(
                    "The booth kept its previous data because these files aren't "
                    "verified yet: " + ", ".join(blocking) + ". The next sync will "
                    "try again."
                )
            else:
                database = _apply_database(ctx, manifest, digest)
            if upcoming:
                ctx.save(force=True, phase="downloading_upcoming")
                file_results.update(_fetch_files(ctx, upcoming))
            programs = _program_results(manifest, file_results)
            ctx.save(force=True, phase="reporting")
            removed, prune_warnings = prune(manifest)
            report.update(
                {
                    "programs": programs,
                    "database": database,
                    "pruned": removed,
                    "warnings": warnings + prune_warnings,
                }
            )
            all_ready = all(p["ready"] for p in programs) and database != (
                "kept_previous"
            )
            ctx.save(
                force=True,
                status=ICSyncRun.Status.SUCCEEDED
                if all_ready
                else ICSyncRun.Status.PARTIAL,
            )
    except RunFailed as exc:
        report["code"] = exc.code
        ctx.save(force=True, status=ICSyncRun.Status.FAILED, error=str(exc))
    except ParentError as exc:
        report["code"] = "unreachable"
        ctx.save(force=True, status=ICSyncRun.Status.FAILED, error=str(exc))
    except Exception as exc:  # noqa: BLE001 - a run must always finish with a status
        logger.exception("Sync run %s failed", run.pk)
        report["code"] = "error"
        ctx.save(
            force=True, status=ICSyncRun.Status.FAILED, error=f"Unexpected error: {exc}"
        )

    state = ICBoothState.get()
    report.update(
        {
            "status": run.status,
            "error": run.error,
            "applied_manifest_hash": state.applied_manifest_hash,
            "verified_files": [
                {
                    "kind": kind,
                    "resource_file_id": ref_id,
                    "xxh64": local.xxh64,
                    "verified_at": local.verified_at.isoformat(),
                    "hashed_this_run": bool(
                        file_results.get((kind, ref_id), {}).get("hashed_this_run")
                    ),
                }
                for local in ICLocalFile.objects.filter(
                    kind=ICLocalFile.Kind.PROJECTION
                )
                for kind, ref_id in [(local.kind, local.ref_id)]
            ],
            "lost_files": [
                {"kind": kind, "resource_file_id": ref_id}
                for (kind, ref_id), result in file_results.items()
                if result.get("lost") and kind == "projection"
            ],
            "disk": _disk(),
        }
    )
    run.report = report
    run.finished_at = timezone.now()
    run.phase = ""
    run.save()

    state.last_run_status = run.status
    state.last_run_error = run.error
    state.last_run_at = run.finished_at
    if run.status in (ICSyncRun.Status.SUCCEEDED, ICSyncRun.Status.UP_TO_DATE):
        state.last_success_at = run.finished_at
    if run.options.get("full_rehash") and run.status in (
        ICSyncRun.Status.SUCCEEDED,
        ICSyncRun.Status.PARTIAL,
    ):
        state.last_full_rehash_at = run.finished_at
    # Only the fields this function owns: the agent's check updates parent_* meanwhile.
    state.save(
        update_fields=[
            "last_run_status",
            "last_run_error",
            "last_run_at",
            "last_success_at",
            "last_full_rehash_at",
        ]
    )

    try:
        client.post_status(report)
    except ParentError as exc:
        run.report = {**report, "status_report_error": str(exc)}
        run.save(update_fields=["report"])
    return run


def _rehash_due(state, now):
    """The weekly re-hash of the only copies of released masters."""
    return (
        timezone.localtime(now).hour in REHASH_HOURS
        and (
            state.last_full_rehash_at is None
            or now - state.last_full_rehash_at >= WEEKLY_REHASH
        )
        and ICLocalFile.objects.filter(available_from_parent=False).exists()
    )


def check_parent(client=None, now=None):
    """The agent's frequent, cheap check (every IC_CHECK_INTERVAL_SECONDS).

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
        data = client.check()
    except ParentError as exc:
        state.parent_check_error = str(exc)
        state.save(update_fields=["parent_check_error"])
        return None
    parent_hash = str(data.get("manifest_hash") or "")[:64]
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
    last = ICSyncRun.objects.exclude(finished_at=None).order_by("-finished_at").first()
    behind = parent_hash != state.applied_manifest_hash or (
        last is not None
        and last.status not in (ICSyncRun.Status.SUCCEEDED, ICSyncRun.Status.UP_TO_DATE)
    )
    if behind and (
        last is None
        # A run that failed before it saw a manifest waits like any other retry, so a
        # broken production endpoint isn't asked again every check.
        or (last.manifest_hash and parent_hash != last.manifest_hash)
        or now - last.finished_at >= RETRY_AFTER
    ):
        return ICSyncRun.objects.create(trigger=ICSyncRun.Trigger.AUTO)
    if _rehash_due(state, now):
        return ICSyncRun.objects.create(
            trigger=ICSyncRun.Trigger.AUTO, options={"full_rehash": True}
        )
    return None


def _disk():
    try:
        usage = shutil.disk_usage(settings.IC_PROJECTION_ROOT)
    except OSError:
        return None
    return {"free": usage.free, "total": usage.total}
