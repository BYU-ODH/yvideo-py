"""Handlers for MediaJob, run by `manage.py process_media_jobs` on production."""

from datetime import timedelta
import logging
import os
from pathlib import Path
import shutil
import time
import uuid

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from core.models import Content
from core.models import ResourceFile
from core.models import projection_file_name

from . import media
from .models import ICInstance
from .models import ICInstanceFile
from .models import ICUpload
from .models import MediaJob
from .sync.bundle import file_xxh64

logger = logging.getLogger(__name__)

ABANDONED_UPLOAD_AGE = timedelta(days=7)


class JobFailed(Exception):
    """A job that cannot succeed; the message is shown to IC editors."""


def queue(kind, *, resource_file=None, payload=None):
    return MediaJob.objects.create(
        kind=kind, resource_file=resource_file, payload=payload or {}
    )


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


def _fail_upload(upload, message):
    upload.status = ICUpload.Status.FAILED
    upload.error = message
    upload.save(update_fields=["status", "error", "updated_at"])
    upload.part_path.unlink(missing_ok=True)
    raise JobFailed(message)


def ingest(job):
    upload = ICUpload.objects.select_related("resource").get(
        pk=job.payload["upload_id"]
    )
    part = upload.part_path
    if not part.exists():
        _fail_upload(
            upload, "The uploaded data is missing on the server; please upload again."
        )

    try:
        probe = media.probe(part)
        if not media.is_faststart(part):
            remuxed = part.with_suffix(".faststart.mp4")
            media.remux_faststart(part, remuxed)
            os.replace(remuxed, part)
            probe = media.probe(part)
    except media.MediaError as exc:
        _fail_upload(upload, str(exc))

    checksum = file_xxh64(part)
    size = part.stat().st_size
    resource = upload.resource
    existing = ResourceFile.objects.filter(checksum=checksum).first()

    if existing is not None:
        if existing.is_projection_file and existing.resource_id == resource.pk:
            if existing.bytes_on_this_server:
                _fail_upload(
                    upload, "This master is already uploaded for this resource."
                )
            # Re-upload of a released master: restore the same row, so the booth's
            # records, fingerprints and derivative links all stay valid.
            destination = Path(existing.file.path)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(part, destination)
            existing.master_released_at = None
            existing.save(update_fields=["master_released_at", "updated_at"])
            upload.status = ICUpload.Status.DONE
            upload.save(update_fields=["status", "updated_at"])
            if not existing.derivatives.exists():
                queue(
                    MediaJob.Kind.TRANSCODE,
                    resource_file=existing,
                    payload={"master_id": existing.pk},
                )
            return f"Restored released master {existing.pk}."
        _fail_upload(
            upload,
            f"A file with the same content already exists: {existing.file.name}.",
        )

    name = projection_file_name(resource.pk, checksum, upload.filename)
    master = ResourceFile(
        resource=resource,
        is_projection_file=True,
        version=f"ic-master-{checksum[:8]}",
        checksum=checksum,
        checksum_at=timezone.now(),
        size_bytes=size,
        faststart=True,
        probed_at=timezone.now(),
        full_video=True,
    )
    probe.apply_to(master)
    master.file.name = name
    # The new bytes go into place first, under a name that contains their checksum, so
    # they can't collide with the previous master. The previous master's bytes are only
    # deleted once the new row has committed: if anything below fails, production still
    # has the previous master, and the upload's bytes go back to where a re-run finds them.
    destination = Path(master.file.storage.path(name))
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(part, destination)
    try:
        with transaction.atomic():
            previous = ResourceFile.objects.filter(
                resource=resource, is_projection_file=True
            ).first()
            switch_ids = []
            if previous is not None:
                switch_ids = list(previous.derivatives.values_list("pk", flat=True))
                if previous.bytes_on_this_server:
                    old_name = previous.file.name
                    transaction.on_commit(lambda: master.file.storage.delete(old_name))
                # A queryset delete, not ResourceFile.delete(), which would remove the
                # bytes at once rather than after the commit.
                ResourceFile.objects.filter(pk=previous.pk).delete()
            master.save()

            upload.status = ICUpload.Status.DONE
            upload.save(update_fields=["status", "updated_at"])
            queue(
                MediaJob.Kind.TRANSCODE,
                resource_file=master,
                payload={
                    "master_id": master.pk,
                    "switch_content_file_ids": switch_ids,
                },
            )
    except Exception:
        shutil.move(destination, part)
        raise
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
        media.transcode_sd(
            source,
            temp_path,
            duration_seconds=master_probe.duration_seconds,
            on_progress=lambda fraction: _set_progress(job, fraction),
        )
        derived_probe = media.probe(temp_path)
        media.assert_same_timeline(master_probe, derived_probe)
    except media.MediaError as exc:
        temp_path.unlink(missing_ok=True)
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
        derived.faststart = True
        derived.probed_at = timezone.now()
        derived.audio_language = master.audio_language
        derived.burned_in_subtitles_language = master.burned_in_subtitles_language
        derived_probe.apply_to(derived)
        derived.save()

        # Contents that used the replaced master's SD file move to the new SD file.
        switch_ids = job.payload.get("switch_content_file_ids") or []
        if switch_ids:
            Content.objects.filter(resource_file_id__in=switch_ids).update(
                resource_file=derived
            )
    maybe_queue_release(master)
    return f"Created SD file {derived.pk} ({derived.width}x{derived.height})."


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
    if not master.derivatives.filter(derived_from=master).exists():
        blockers.append("the SD transcode has not finished")
    booths = list(ICInstance.objects.all())
    if not booths:
        blockers.append("no booth is configured")
    for booth in booths:
        copy = ICInstanceFile.objects.filter(
            instance=booth, resource_file=master
        ).first()
        if copy is None or not copy.hashed_from_bytes or not copy.matches():
            blockers.append(f"{booth.name} has not verified its copy yet")
    return blockers


def maybe_queue_release(master):
    if release_blockers(master):
        return None
    pending = MediaJob.objects.filter(
        kind=MediaJob.Kind.RELEASE_MASTER,
        resource_file=master,
        status__in=[MediaJob.Status.QUEUED, MediaJob.Status.RUNNING],
    ).first()
    return pending or queue(
        MediaJob.Kind.RELEASE_MASTER,
        resource_file=master,
        payload={"master_id": master.pk},
    )


def release_master(job):
    with transaction.atomic():
        master = ResourceFile.objects.select_for_update().get(
            pk=job.payload["master_id"]
        )
        blockers = release_blockers(master)
        if blockers == ["already released"]:
            return "Already released."
        if blockers:
            raise JobFailed("Not releasing: " + "; ".join(blockers) + ".")
        if master.bytes_on_this_server:
            master.file.storage.delete(master.file.name)
        master.master_released_at = timezone.now()
        master.save(update_fields=["master_released_at", "updated_at"])
    return f"Released production's copy of master {master.pk}; the booth holds the only copy."


# ---------------------------------------------------------------------------
# Clean up abandoned uploads
# ---------------------------------------------------------------------------


def cleanup_uploads(now=None):
    """Mark uploads idle for a week abandoned and delete their partial data.

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
        upload.status = ICUpload.Status.ABANDONED
        upload.save(update_fields=["status", "updated_at"])
        count += 1
    return count


HANDLERS = {
    MediaJob.Kind.INGEST: ingest,
    MediaJob.Kind.TRANSCODE: transcode,
    MediaJob.Kind.RELEASE_MASTER: release_master,
}


def claim_next():
    """Atomically claim the oldest queued job, or None."""
    for candidate in MediaJob.objects.filter(status=MediaJob.Status.QUEUED).order_by(
        "created_at", "id"
    )[:5]:
        claimed = MediaJob.objects.filter(
            pk=candidate.pk, status=MediaJob.Status.QUEUED
        ).update(status=MediaJob.Status.RUNNING, started_at=timezone.now())
        if claimed:
            candidate.refresh_from_db()
            return candidate
    return None


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
