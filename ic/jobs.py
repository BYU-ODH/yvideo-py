"""Handlers for MediaJob, run by `manage.py process_media_jobs` on production."""

from datetime import timedelta
import logging
import os
from pathlib import Path
import time
import uuid

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from core.models import Content
from core.models import ResourceFile
from core.models import projection_file_name

from . import media
from .models import ICBoothReport
from .models import ICUpload
from .models import MediaJob
from .sync.bundle import file_xxh64

logger = logging.getLogger(__name__)

ABANDONED_UPLOAD_AGE = timedelta(days=7)


class JobFailed(Exception):
    """A job that cannot succeed; the message is shown to IC editors."""


def _set_progress(job, fraction):
    """Record a running job's progress, at most every 2 seconds."""
    now = time.monotonic()
    if now - getattr(job, "_progress_saved_at", 0.0) < 2 and fraction < 1:
        return
    job._progress_saved_at = now
    MediaJob.objects.filter(pk=job.pk).update(progress=round(fraction, 4))


# ---------------------------------------------------------------------------
# Ingest: an uploaded .part becomes a projection master
# ---------------------------------------------------------------------------


def ingest(job):
    """Probe, hash and store an upload as its resource's master, then queue the SD file.

    Whatever goes wrong, the upload is marked failed with the reason and its bytes are
    deleted. The bytes move into place (a rename within IC_PROJECTION_ROOT) as the last
    step of the transaction, so a failure never leaves a half-made master behind, and
    the previous master's bytes are deleted only after the commit.
    """
    upload = ICUpload.objects.select_related("resource").get(
        pk=job.payload["upload_id"]
    )
    try:
        return _ingest(upload)
    except Exception as exc:
        upload.status = ICUpload.Status.FAILED
        upload.error = (
            str(exc) if isinstance(exc, JobFailed) else f"Unexpected error: {exc}"
        )
        upload.save(update_fields=["status", "error", "updated_at"])
        upload.part_path.unlink(missing_ok=True)
        raise


def _move_into_place(part, destination):
    destination.parent.mkdir(parents=True, exist_ok=True)
    os.replace(part, destination)


def _ingest(upload):
    part = upload.part_path
    if not part.exists():
        raise JobFailed(
            "The uploaded data is missing on the server; please upload again."
        )
    try:
        probe = media.probe(part)
    except media.MediaError as exc:
        raise JobFailed(str(exc)) from exc

    checksum = file_xxh64(part)
    resource = upload.resource
    existing = ResourceFile.objects.filter(checksum=checksum).first()
    if existing is not None:
        if not (existing.is_projection_file and existing.resource_id == resource.pk):
            raise JobFailed(
                f"A file with the same content already exists: {existing.file.name}."
            )
        if existing.bytes_on_this_server:
            raise JobFailed("This master is already uploaded for this resource.")
        # Re-upload of a released master: restore the same row, so the booth's
        # records, fingerprints and derivative links all stay valid.
        with transaction.atomic():
            existing.master_released_at = None
            existing.save(update_fields=["master_released_at", "updated_at"])
            upload.status = ICUpload.Status.DONE
            upload.save(update_fields=["status", "updated_at"])
            if not existing.derivatives.exists():
                MediaJob.objects.create(
                    kind=MediaJob.Kind.TRANSCODE,
                    resource_file=existing,
                    payload={"master_id": existing.pk},
                )
            _move_into_place(part, Path(existing.file.path))
        return f"Restored released master {existing.pk}."

    size = part.stat().st_size
    master = ResourceFile(
        resource=resource,
        is_projection_file=True,
        version=f"ic-master-{checksum[:8]}",
        checksum=checksum,
        checksum_at=timezone.now(),
        size_bytes=size,
        full_video=True,
    )
    probe.apply_to(master)
    # The name contains the checksum, so it can't collide with the previous master.
    master.file.name = projection_file_name(resource.pk, checksum, upload.filename)
    with transaction.atomic():
        previous = ResourceFile.objects.filter(
            resource=resource, is_projection_file=True
        ).first()
        replaced_ids = []
        if previous is not None:
            replaced_ids = list(previous.derivatives.values_list("pk", flat=True))
            if previous.bytes_on_this_server:
                old_name = previous.file.name
                transaction.on_commit(lambda: master.file.storage.delete(old_name))
            # A queryset delete, not ResourceFile.delete(), which would remove the
            # bytes at once rather than after the commit.
            ResourceFile.objects.filter(pk=previous.pk).delete()
        master.save()
        upload.status = ICUpload.Status.DONE
        upload.save(update_fields=["status", "updated_at"])
        MediaJob.objects.create(
            kind=MediaJob.Kind.TRANSCODE,
            resource_file=master,
            payload={"master_id": master.pk, "replaces_file_ids": replaced_ids},
        )
        _move_into_place(part, Path(master.file.storage.path(master.file.name)))
    return f"Created master {master.pk} ({size} bytes)."


# ---------------------------------------------------------------------------
# Transcode: master → SD editing file on the same timeline
# ---------------------------------------------------------------------------


def transcode(job):
    master = ResourceFile.objects.select_related("resource").get(
        pk=job.payload["master_id"]
    )
    if not master.bytes_on_this_server:
        raise JobFailed(
            "The master's bytes are not on this server; re-upload it first."
        )
    source = Path(master.file.path)
    master_probe = media.probe(source)

    version = f"ic-sd-{master.checksum[:8]}"
    relative = f"{master.resource.name}/{version}.mp4"
    final_path = Path(settings.MEDIA_ROOT) / relative
    temp_path = final_path.with_name(f".{final_path.stem}.{uuid.uuid4().hex}.mp4")
    try:
        try:
            media.transcode_sd(
                source,
                temp_path,
                duration_seconds=master_probe.duration_seconds,
                on_progress=lambda fraction: _set_progress(job, fraction),
            )
            derived_probe = media.probe(temp_path)
            media.assert_same_timeline(master_probe, derived_probe)
        except media.MediaError as exc:
            raise JobFailed(str(exc)) from exc
        checksum = file_xxh64(temp_path)
        with transaction.atomic():
            derived = ResourceFile.objects.filter(
                resource=master.resource, version=version
            ).first()
            if derived is None:
                derived = ResourceFile(resource=master.resource, version=version)
            elif (
                derived.file
                and derived.bytes_on_this_server
                and derived.file.name != relative
            ):
                derived.file.delete(save=False)
            final_path.parent.mkdir(parents=True, exist_ok=True)
            os.replace(temp_path, final_path)
            derived.file.name = relative
            derived.checksum = checksum
            derived.checksum_at = timezone.now()
            derived.derived_from = master
            derived.size_bytes = final_path.stat().st_size
            derived.audio_language = master.audio_language
            derived.burned_in_subtitles_language = master.burned_in_subtitles_language
            derived_probe.apply_to(derived)
            derived.save()

            # Contents on the replaced master's SD files move to the new one, and the
            # old SD files go: nothing else uses them.
            replaced_ids = job.payload.get("replaces_file_ids") or []
            Content.objects.filter(resource_file_id__in=replaced_ids).update(
                resource_file=derived
            )
            for old in ResourceFile.objects.filter(pk__in=replaced_ids).exclude(
                pk=derived.pk
            ):
                transaction.on_commit(lambda f=old.file: f.storage.delete(f.name))
                ResourceFile.objects.filter(pk=old.pk).delete()
    finally:
        # A failed or interrupted transcode leaves no partial file in MEDIA_ROOT.
        temp_path.unlink(missing_ok=True)
    release_if_ready(master)
    return (
        f"Created SD file {derived.pk} ({derived_probe.width}x{derived_probe.height})."
    )


# ---------------------------------------------------------------------------
# Release: delete production's copy once the booth holds a verified one
# ---------------------------------------------------------------------------


def release_blockers(master):
    """Why production must keep this master's bytes; empty when it may release them."""
    blockers = []
    if not master.is_projection_file:
        return ["not a projection master"]
    if master.master_released_at:
        return ["already released"]
    if not master.derivatives.exists():
        blockers.append("the SD transcode has not finished")
    if not ICBoothReport.get().holds_verified_copy(master):
        blockers.append("the booth has not verified its copy yet")
    return blockers


def release_if_ready(master):
    """Delete production's copy of a master once nothing blocks it (see release_blockers)."""
    with transaction.atomic():
        master = ResourceFile.objects.select_for_update().get(pk=master.pk)
        if release_blockers(master):
            return False
        if master.bytes_on_this_server:
            name = master.file.name
            transaction.on_commit(lambda: master.file.storage.delete(name))
        master.master_released_at = timezone.now()
        master.save(update_fields=["master_released_at", "updated_at"])
    logger.info("Released production's copy of master %s.", master.pk)
    return True


# ---------------------------------------------------------------------------
# Clean up abandoned uploads
# ---------------------------------------------------------------------------


def cleanup_uploads(now=None):
    """Delete uploads idle for a week, with their partial data.

    Called by the media worker every few hours. Not a MediaJob: it isn't something an
    IC editor asked for, so it stays out of the dashboard's job list.
    """
    cutoff = (now or timezone.now()) - ABANDONED_UPLOAD_AGE
    stale = ICUpload.objects.filter(
        status=ICUpload.Status.UPLOADING, updated_at__lt=cutoff
    )
    count = 0
    for upload in stale:
        upload.part_path.unlink(missing_ok=True)
        upload.delete()
        count += 1
    return count


HANDLERS = {
    MediaJob.Kind.INGEST: ingest,
    MediaJob.Kind.TRANSCODE: transcode,
}


def claim_next():
    """Claim the oldest queued job, or None. One media worker runs per server."""
    job = MediaJob.objects.filter(status=MediaJob.Status.QUEUED).first()
    if job is not None:
        job.status = MediaJob.Status.RUNNING
        job.started_at = timezone.now()
        job.save(update_fields=["status", "started_at"])
    return job


def run(job):
    handler = HANDLERS[job.kind]
    try:
        message = handler(job)
    except JobFailed as exc:
        job.status = MediaJob.Status.FAILED
        job.error = str(exc)
    except Exception as exc:  # noqa: BLE001 - recorded on the job for IC editors
        logger.exception("Media job %s failed", job.pk)
        job.status = MediaJob.Status.FAILED
        job.error = f"Unexpected error: {exc}"
    else:
        job.status = MediaJob.Status.SUCCEEDED
        job.log = message or ""
        job.progress = 1.0
    job.finished_at = timezone.now()
    job.save()
    return job


def recover_interrupted_jobs():
    """Jobs left RUNNING by a crashed worker are retried; every handler is restartable."""
    return MediaJob.objects.filter(status=MediaJob.Status.RUNNING).update(
        status=MediaJob.Status.QUEUED, started_at=None
    )
