"""One booth sync run, whatever started it (IC_MODE_PLAN.md sections 7.3-7.4, 7.8).

Every trigger runs this same pipeline. Forcing changes when it runs and how thoroughly
it checks (always re-import and re-verify, optionally re-hash every file), never whether
it verifies.
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

PLAYBACK_WAIT_POLL_SECONDS = 10
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
            f"{local_sha[:10]}. The booth deploy timer will catch up within minutes.",
        )


def _verify_files(ctx, manifest, *, full_rehash):
    """Make every manifest file present and verified locally. Returns per-file results."""
    entries = sorted(manifest.get("files", []), key=_sort_key)
    results = {}
    to_hash_or_fetch = []
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
            local.available_from_parent = entry.get("available_from_parent", True)
            if entry.get("last_program_end"):
                local.last_program_end = date.fromisoformat(entry["last_program_end"])
            local.save()
        if trusted:
            results[(entry["kind"], _ref_id(entry))] = {
                "ok": True,
                "hashed_this_run": False,
            }
        else:
            to_hash_or_fetch.append((entry, path, on_disk))

    needed = sum(entry["size"] for entry, _, on_disk in to_hash_or_fetch if not on_disk)
    ctx.save(
        force=True,
        phase="downloading",
        files_total=len(entries),
        files_done=len(entries) - len(to_hash_or_fetch),
        bytes_total=sum(entry["size"] for entry, _, _ in to_hash_or_fetch),
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

    for entry, path, on_disk in to_hash_or_fetch:
        key = (entry["kind"], _ref_id(entry))
        ok = False
        if on_disk:
            ok = file_xxh64(path) == entry["xxh64"]
            ctx.add_bytes(entry["size"])
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
                    ctx.client.download(
                        entry,
                        part,
                        on_bytes=ctx.add_bytes,
                        throttled=booth_state.playback_active,
                    )
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
                    "last_program_end": (
                        date.fromisoformat(entry["last_program_end"])
                        if entry.get("last_program_end")
                        else None
                    ),
                },
            )
            results[key] = {"ok": True, "hashed_this_run": True}
        ctx.save(files_done=ctx.run.files_done + 1)
    return results


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


def _wait_for_playback(ctx):
    if ctx.run.options.get("apply_during_playback"):
        return
    while booth_state.playback_active():
        ctx.save(
            force=True, status=ICSyncRun.Status.WAITING_FOR_PLAYBACK, phase="waiting"
        )
        time.sleep(PLAYBACK_WAIT_POLL_SECONDS)
    ctx.save(force=True, status=ICSyncRun.Status.RUNNING)


def _apply_database(ctx, manifest, manifest_digest):
    state = ICBoothState.get()
    fixture_sha = manifest["fixture"]["sha256"]
    if not ctx.run.is_forced and fixture_sha == state.applied_fixture_sha256:
        return "unchanged"
    _wait_for_playback(ctx)
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
    state.applied_manifest_hash = manifest_digest
    state.applied_fixture_sha256 = fixture_sha
    state.applied_manifest = manifest
    state.applied_at = timezone.now()
    state.save()
    return "applied"


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
    report = {
        "run_id": str(run.pk),
        "request_id": str(run.request_id) if run.request_id else None,
        "trigger": run.trigger,
    }
    file_results = {}
    manifest = None
    try:
        manifest, digest = client.manifest(
            if_none_match=None
            if run.is_forced or run.options.get("full_rehash")
            else state.applied_manifest_hash or None
        )
        if manifest is None:
            ctx.save(
                force=True,
                status=ICSyncRun.Status.UP_TO_DATE,
                manifest_hash=digest or "",
            )
        else:
            ctx.save(force=True, manifest_hash=digest)
            _check_code_version(manifest)
            file_results = _verify_files(
                ctx, manifest, full_rehash=run.options.get("full_rehash", False)
            )
            failed_codes = [
                r.get("code") for r in file_results.values() if not r.get("ok")
            ]
            if "checksum_failed" in failed_codes and not any(
                r.get("ok") for r in file_results.values()
            ):
                raise RunFailed(
                    "checksum_failed",
                    next(
                        r["error"]
                        for r in file_results.values()
                        if r.get("code") == "checksum_failed"
                    ),
                )
            programs = _program_results(manifest, file_results)
            database = "skipped"
            if not programs or any(p["files_ready"] for p in programs):
                database = _apply_database(ctx, manifest, digest)
            ctx.save(force=True, phase="reporting")
            removed, warnings = prune(manifest)
            report.update(
                {
                    "programs": programs,
                    "database": database,
                    "pruned": removed,
                    "warnings": warnings,
                }
            )
            all_ready = all(p["ready"] for p in programs) and database != "skipped"
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
    state.save()

    try:
        client.post_status(report)
    except ParentError as exc:
        run.report = {**report, "status_report_error": str(exc)}
        run.save(update_fields=["report"])
    return run


def _disk():
    try:
        usage = shutil.disk_usage(settings.IC_PROJECTION_ROOT)
    except OSError:
        return None
    return {"free": usage.free, "total": usage.total}
