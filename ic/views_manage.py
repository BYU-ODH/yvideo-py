"""IC management on production (IC_MODE_PLAN.md section 4.7): standard Y-video pages."""

from datetime import timedelta
import json
import logging
import shutil

from django.conf import settings
from django.contrib import messages
from django.db import transaction
from django.db.models import Q
from django.http import HttpResponse
from django.http import HttpResponseBadRequest
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.shortcuts import redirect
from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET
from django.views.decorators.http import require_http_methods
from django.views.decorators.http import require_POST
import reversion

from core.models import Content
from core.models import Resource

from . import readiness
from .forms import ICProgramForm
from .forms import ICProgramItemForm
from .jobs import queue
from .jobs import release_blockers
from .models import ICInstance
from .models import ICProgram
from .models import ICProgramItem
from .models import ICSyncRequest
from .models import ICUpload
from .models import MediaJob
from .permissions import ic_editor_required
from .permissions import ic_program_write_required
from .permissions import ic_resource_upload_required
from .permissions import ic_sync_request_read_required
from .permissions import ic_upload_owner_required
from .sync.bundle import cached_manifest_hash
from .sync.bundle import current_manifest_hash

logger = logging.getLogger(__name__)

# Free space an upload needs: the .part itself, a possible faststart remux, and the SD file.
UPLOAD_SPACE_FACTOR = 2.2
# The upload page slices files into chunks of this size; each is one PUT.
UPLOAD_CHUNK_BYTES = 16 * 1024 * 1024


def ic_trail(*crumbs):
    return [
        {"label": label, "url": url}
        for label, url in (
            ("International Cinema", reverse("ic_manage_dashboard")),
            *crumbs,
        )
    ]


def _record(request, comment):
    reversion.set_user(getattr(request, "original_user", request.user))
    reversion.set_comment(comment)


def _program_row(program):
    problems = readiness.program_problems(program)
    return {
        "program": program,
        "problems": problems,
        "status": readiness.rollup(problems),
    }


def _instance_rows(current_hash):
    rows = []
    for instance in ICInstance.objects.order_by("name"):
        status = instance.last_status or {}
        rows.append(
            {
                "instance": instance,
                "status": status,
                "matches": bool(current_hash)
                and instance.last_applied_manifest_hash == current_hash,
                "programs": {p.get("id"): p for p in status.get("programs", [])},
                "in_contact": instance.checked_in_recently(),
                "recent_requests": instance.sync_requests.all()[:5],
            }
        )
    return rows


@require_GET
@ic_editor_required
def dashboard(request):
    today = timezone.localdate()
    programs = list(ICProgram.objects.filter(end_date__gte=today - timedelta(days=30)))
    try:
        current_hash = cached_manifest_hash()
    except Exception:  # noqa: BLE001 - the dashboard must render even if a file is unreadable
        logger.exception("Could not compute the IC manifest hash")
        current_hash = ""
    instances = _instance_rows(current_hash)
    return render(
        request,
        "ic/manage/dashboard.html",
        {
            "breadcrumbs": ic_trail(),
            "active": [_program_row(p) for p in programs if p.is_active(today)],
            "upcoming": [_program_row(p) for p in programs if p.is_upcoming(today)],
            "past": [_program_row(p) for p in programs if p.is_past(today)],
            "instances": instances,
            "current_hash": current_hash,
            "check_interval": settings.IC_CHECK_INTERVAL_SECONDS,
            "jobs": MediaJob.objects.order_by("-created_at")[:10],
        },
    )


@require_GET
@ic_editor_required
def jobs_partial(request):
    return render(
        request,
        "ic/manage/partials/jobs.html",
        {"jobs": MediaJob.objects.order_by("-created_at")[:10]},
    )


# ---------------------------------------------------------------------------
# Programs
# ---------------------------------------------------------------------------


@require_GET
@ic_editor_required
def program_new(request):
    today = timezone.localdate()
    form = ICProgramForm(initial={"start_date": today, "end_date": today})
    return render(
        request,
        "ic/manage/program_new.html",
        {"form": form, "breadcrumbs": ic_trail(("New program", None))},
    )


@require_POST
@ic_editor_required
def program_create(request):
    form = ICProgramForm(request.POST)
    if not form.is_valid():
        return render(
            request,
            "ic/manage/program_new.html",
            {"form": form, "breadcrumbs": ic_trail(("New program", None))},
            status=400,
        )
    with transaction.atomic(), reversion.create_revision():
        program = form.save()
        _record(request, "Created IC program")
    messages.success(
        request, f'Program "{program.name}" created. Now add what it shows.'
    )
    return redirect("ic_program_edit", program.pk)


def _available_contents():
    return (
        Content.objects.filter(playlist__is_ic=True)
        .select_related("playlist", "resource")
        .order_by("playlist__name", "title")
    )


def _program_edit_context(program, form=None):
    items = list(program.ordered_items())
    rows = []
    for item in items:
        problems = readiness.item_problems(item)
        rows.append(
            {
                "item": item,
                "form": ICProgramItemForm(instance=item, prefix=f"item-{item.pk}"),
                "problems": problems,
                "status": readiness.rollup(problems),
            }
        )
    problems = readiness.program_problems(program)
    return {
        "program": program,
        "form": form or ICProgramForm(instance=program),
        "rows": rows,
        "status": readiness.rollup(problems),
        "available_contents": _available_contents(),
        "breadcrumbs": ic_trail((program.name, None)),
    }


@require_GET
@ic_program_write_required
def program_edit(request, program):
    return render(
        request, "ic/manage/program_edit.html", _program_edit_context(program)
    )


@require_POST
@ic_program_write_required
def program_update(request, program):
    form = ICProgramForm(request.POST, instance=program)
    if not form.is_valid():
        return render(
            request,
            "ic/manage/program_edit.html",
            _program_edit_context(program, form),
            status=400,
        )
    with transaction.atomic(), reversion.create_revision():
        form.save()
        _record(request, "Updated IC program")
    messages.success(request, "Program saved.")
    return redirect("ic_program_edit", program.pk)


@require_POST
@ic_program_write_required
def program_delete(request, program):
    name = program.name
    with transaction.atomic(), reversion.create_revision():
        program.delete()
        _record(request, "Deleted IC program")
    messages.success(request, f'Program "{name}" deleted.')
    return redirect("ic_manage_dashboard")


def _program_item(program, item_id):
    return get_object_or_404(ICProgramItem, pk=item_id, program=program)


@require_POST
@ic_program_write_required
def item_add(request, program):
    content = get_object_or_404(
        Content.objects.filter(playlist__is_ic=True), pk=request.POST.get("content_id")
    )
    mode = request.POST.get("mode", ICProgramItem.Mode.ONCE)
    if mode not in ICProgramItem.Mode.values:
        return HttpResponseBadRequest("Unknown mode.")
    position = program.items.count()
    # VTT is the preferred kind of subtitles; a content without a default track is
    # most likely an announcement or has burned-in subtitles, which the editor sets.
    subtitles = (
        ICProgramItem.Subtitles.VTT
        if content.default_subtitle_track_id
        else ICProgramItem.Subtitles.NONE
    )
    with transaction.atomic(), reversion.create_revision():
        ICProgramItem.objects.create(
            program=program,
            content=content,
            position=position,
            mode=mode,
            subtitles=subtitles,
        )
        _record(request, "Added IC program item")
    return redirect("ic_program_edit", program.pk)


@require_POST
@ic_program_write_required
def item_update(request, program, item_id):
    item = _program_item(program, item_id)
    form = ICProgramItemForm(request.POST, instance=item, prefix=f"item-{item.pk}")
    if not form.is_valid():
        return HttpResponseBadRequest("Invalid item settings.")
    with transaction.atomic(), reversion.create_revision():
        form.save()
        _record(request, "Updated IC program item")
    messages.success(request, f'Saved "{item.content.title}".')
    return redirect("ic_program_edit", program.pk)


@require_POST
@ic_program_write_required
def item_delete(request, program, item_id):
    item = _program_item(program, item_id)
    with transaction.atomic(), reversion.create_revision():
        item.delete()
        program.renumber_items()
        _record(request, "Removed IC program item")
    return redirect("ic_program_edit", program.pk)


@require_POST
@ic_program_write_required
def items_reorder(request, program):
    try:
        item_ids = [int(i) for i in json.loads(request.body)["item_ids"]]
    except (ValueError, KeyError, TypeError):
        return HttpResponseBadRequest('Expected {"item_ids": [...]}.')
    existing = set(program.items.values_list("pk", flat=True))
    if set(item_ids) != existing or len(item_ids) != len(existing):
        return HttpResponseBadRequest("The item list doesn't match the program.")
    with transaction.atomic(), reversion.create_revision():
        for position, item_id in enumerate(item_ids):
            ICProgramItem.objects.filter(pk=item_id, program=program).update(
                position=position
            )
        _record(request, "Reordered IC program items")
    return HttpResponse(status=204)


@require_POST
@ic_program_write_required
def item_move(request, program, item_id):
    """Keyboard-friendly reorder: move one item up or down."""
    item = _program_item(program, item_id)
    items = list(program.items.order_by("position", "id"))
    index = items.index(item)
    target = index - 1 if request.POST.get("direction") == "up" else index + 1
    if 0 <= target < len(items):
        items[index], items[target] = items[target], items[index]
        with transaction.atomic(), reversion.create_revision():
            for position, each in enumerate(items):
                if each.position != position:
                    ICProgramItem.objects.filter(pk=each.pk).update(position=position)
            _record(request, "Reordered IC program items")
    return redirect("ic_program_edit", program.pk)


# ---------------------------------------------------------------------------
# Resources and projection masters
# ---------------------------------------------------------------------------


def _resource_rows(user):
    ic_resource_ids = Content.objects.filter(playlist__is_ic=True).values_list(
        "resource_id", flat=True
    )
    resources = Resource.objects.filter(Q(pk__in=ic_resource_ids)).order_by("name")
    rows = []
    for resource in resources:
        master = resource.resource_files.filter(is_projection_file=True).first()
        derived = master.derivatives.first() if master else None
        transcode = (
            MediaJob.objects.filter(kind=MediaJob.Kind.TRANSCODE, resource_file=master)
            .order_by("-created_at")
            .first()
            if master
            else None
        )
        ic_contents = Content.objects.filter(resource=resource, playlist__is_ic=True)
        rows.append(
            {
                "resource": resource,
                "master": master,
                "derived": derived,
                "transcode": transcode,
                "release_blockers": release_blockers(master) if master else [],
                "uploads": resource.ic_uploads.exclude(status=ICUpload.Status.DONE)[:3],
                "ic_contents": ic_contents,
                "contents_not_on_sd": [
                    c
                    for c in ic_contents
                    if derived and c.resource_file_id != derived.pk
                ],
                "can_upload": user.can_access_resource(resource),
            }
        )
    return rows


@require_GET
@ic_editor_required
def resources(request):
    return render(
        request,
        "ic/manage/resources.html",
        {
            "rows": _resource_rows(request.user),
            "breadcrumbs": ic_trail(("Films and masters", None)),
        },
    )


@require_POST
@ic_resource_upload_required
def resource_use_sd(request, resource):
    master = resource.resource_files.filter(is_projection_file=True).first()
    derived = master.derivatives.first() if master else None
    if derived is None:
        messages.error(request, "There's no SD file made from the master yet.")
        return redirect("ic_manage_resources")
    count = (
        Content.objects.filter(resource=resource, playlist__is_ic=True)
        .exclude(resource_file=derived)
        .update(resource_file=derived)
    )
    messages.success(
        request,
        f"{count} IC content(s) now use the SD file made from the master. Check that "
        "existing annotations still line up if the old file came from a different source.",
    )
    return redirect("ic_manage_resources")


@require_GET
@ic_resource_upload_required
def projection_upload_page(request, resource):
    master = resource.resource_files.filter(is_projection_file=True).first()
    return render(
        request,
        "ic/manage/projection_upload.html",
        {
            "resource": resource,
            "master": master,
            "chunk_bytes": UPLOAD_CHUNK_BYTES,
            "breadcrumbs": ic_trail(
                ("Films and masters", reverse("ic_manage_resources")),
                (f"Upload master: {resource.name}", None),
            ),
        },
    )


def _upload_json(upload):
    return {
        "id": str(upload.pk),
        "filename": upload.filename,
        "expected_size": upload.expected_size,
        "received_bytes": upload.received_bytes,
        "status": upload.status,
        "error": upload.error,
        "job": (
            {
                "status": upload.job.status,
                "progress": upload.job.progress,
                "error": upload.job.error,
            }
            if upload.job_id
            else None
        ),
        "chunk_url": reverse("ic_upload_chunk", args=[upload.pk]),
        "finalize_url": reverse("ic_upload_finalize", args=[upload.pk]),
        "status_url": reverse("ic_upload_status", args=[upload.pk]),
    }


@require_POST
@ic_resource_upload_required
def upload_create(request, resource):
    try:
        data = json.loads(request.body)
        filename = str(data["filename"])[:255]
        size = int(data["size"])
    except (ValueError, KeyError, TypeError):
        return HttpResponseBadRequest('Expected {"filename": ..., "size": ...}.')
    if size <= 0:
        return HttpResponseBadRequest("The file is empty.")
    if not filename.lower().endswith((".mp4", ".m4v")):
        return HttpResponseBadRequest("Projection masters must be .mp4 or .m4v files.")

    # Resume an unfinished upload of the same file instead of starting over.
    existing = ICUpload.objects.filter(
        resource=resource,
        owner=request.user,
        filename=filename,
        expected_size=size,
        status=ICUpload.Status.UPLOADING,
    ).first()
    if existing is not None and existing.part_path.exists():
        existing.received_bytes = existing.part_path.stat().st_size
        existing.save(update_fields=["received_bytes", "updated_at"])
        return JsonResponse(_upload_json(existing))

    root = settings.IC_PROJECTION_ROOT
    root.mkdir(parents=True, exist_ok=True)
    needed = int(size * UPLOAD_SPACE_FACTOR)
    free = shutil.disk_usage(root).free
    if free < needed:
        return HttpResponse(
            f"Not enough space on the server: {free / 1e9:.1f} GB free, about "
            f"{needed / 1e9:.1f} GB needed. Ask an administrator to free space.",
            status=507,
            content_type="text/plain",
        )
    upload = ICUpload.objects.create(
        resource=resource, owner=request.user, filename=filename, expected_size=size
    )
    upload.part_path.parent.mkdir(parents=True, exist_ok=True)
    upload.part_path.touch()
    return JsonResponse(_upload_json(upload), status=201)


@require_GET
@ic_upload_owner_required
def upload_status(request, upload):
    if upload.status == ICUpload.Status.UPLOADING and upload.part_path.exists():
        upload.received_bytes = upload.part_path.stat().st_size
    return JsonResponse(_upload_json(upload))


@require_http_methods(["PUT"])
@ic_upload_owner_required
def upload_chunk(request, upload):
    if upload.status != ICUpload.Status.UPLOADING:
        return HttpResponse("This upload is no longer accepting data.", status=409)
    try:
        offset = int(request.GET["offset"])
    except (KeyError, ValueError):
        return HttpResponseBadRequest("Missing ?offset=.")
    part = upload.part_path
    current = part.stat().st_size if part.exists() else 0
    if offset != current:
        return JsonResponse(
            {"error": "offset mismatch", "received_bytes": current}, status=409
        )
    written = 0
    # request.read() streams the body; request.body would load it into memory and
    # trip DATA_UPLOAD_MAX_MEMORY_SIZE.
    with open(part, "ab") as handle:
        while True:
            chunk = request.read(1024 * 1024)
            if not chunk:
                break
            if current + written + len(chunk) > upload.expected_size:
                handle.truncate(current)
                return HttpResponseBadRequest(
                    "More data than the file's declared size."
                )
            handle.write(chunk)
            written += len(chunk)
    upload.received_bytes = current + written
    upload.save(update_fields=["received_bytes", "updated_at"])
    return JsonResponse({"received_bytes": upload.received_bytes})


@require_POST
@ic_upload_owner_required
def upload_finalize(request, upload):
    if upload.status != ICUpload.Status.UPLOADING:
        return JsonResponse(_upload_json(upload))
    size = upload.part_path.stat().st_size if upload.part_path.exists() else 0
    if size != upload.expected_size:
        return JsonResponse(
            {
                "error": f"Received {size} of {upload.expected_size} bytes.",
                "received_bytes": size,
            },
            status=409,
        )
    upload.received_bytes = size
    upload.status = ICUpload.Status.PROCESSING
    upload.job = queue(MediaJob.Kind.INGEST, payload={"upload_id": str(upload.pk)})
    upload.save(update_fields=["received_bytes", "status", "job", "updated_at"])
    return JsonResponse(_upload_json(upload))


# ---------------------------------------------------------------------------
# Force push
#
# Production never connects to the booth. A push is an ICSyncRequest that the booth
# collects on its next check (every IC_CHECK_INTERVAL_SECONDS); the booth then posts its
# progress and final report here, and this indicator shows what it last said.
# ---------------------------------------------------------------------------

# A running push with no progress report for this long has probably lost contact.
PROGRESS_STALE = timedelta(seconds=20)


def _bool(request, name):
    return request.POST.get(name) in ("1", "true", "on", "yes")


@require_POST
@ic_editor_required
def instance_push(request, instance_id):
    instance = get_object_or_404(ICInstance, pk=instance_id)
    ICSyncRequest.expire_stale()
    # One waiting push is enough: a second click shows the first one's indicator.
    sync_request = ICSyncRequest.objects.filter(
        instance=instance, status=ICSyncRequest.Status.PENDING
    ).first()
    if sync_request is None:
        try:
            manifest_digest = current_manifest_hash()
        except Exception:  # noqa: BLE001 - the push still works; "matches" can't be shown
            logger.exception("Could not compute the IC manifest hash")
            manifest_digest = ""
        user = getattr(request, "original_user", request.user)
        sync_request = ICSyncRequest.objects.create(
            instance=instance,
            requested_by=user,
            requested_by_name=user.get_full_name() or user.username,
            manifest_hash_at_request=manifest_digest,
            options={"full_rehash": _bool(request, "full_rehash")},
        )
    return render(
        request,
        "ic/manage/partials/sync_request.html",
        _sync_request_context(sync_request),
    )


def _sync_request_context(sync_request):
    report = sync_request.report or {}
    try:
        current = cached_manifest_hash()
    except Exception:  # noqa: BLE001
        current = ""
    applied = (
        report.get("applied_manifest_hash")
        or sync_request.instance.last_applied_manifest_hash
    )
    now = timezone.now()
    return {
        "sync_request": sync_request,
        "live": None if sync_request.is_final else (sync_request.progress or None),
        "report": report,
        "booth_in_contact": sync_request.instance.checked_in_recently(now),
        "check_interval": settings.IC_CHECK_INTERVAL_SECONDS,
        "expires_at": sync_request.created_at + ICSyncRequest.PENDING_TTL,
        "progress_stale": (
            not sync_request.is_final
            and sync_request.progress_at is not None
            and now - sync_request.progress_at > PROGRESS_STALE
        ),
        "matches_current": bool(applied) and applied == current,
        "matches_request": bool(applied)
        and applied == sync_request.manifest_hash_at_request,
    }


@require_GET
@ic_sync_request_read_required
def sync_request_detail(request, sync_request):
    if ICSyncRequest.expire_stale():
        sync_request.refresh_from_db()
    return render(
        request,
        "ic/manage/partials/sync_request.html",
        _sync_request_context(sync_request),
    )
