"""Production's sync API, called by booths with their bearer token.

Sync is pull-only. Besides fetching data, a booth checks in every
CHECK_INTERVAL_SECONDS (`check`), which is how it learns about changes, and reports
each run's outcome (`status`).
"""

from functools import wraps
import json

from django.contrib.auth.decorators import login_not_required
from django.core.cache import cache
from django.db import transaction
from django.http import Http404
from django.http import HttpResponse
from django.http import HttpResponseBadRequest
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET
from django.views.decorators.http import require_POST

from core.models import ResourceFile
from core.models import Subtitle
from core.views import _file_response

from .jobs import release_if_ready
from .models import ICInstance
from .models import ICInstanceFile
from .models import ICProgramItem
from .sync.bundle import MANIFEST_HASH_CACHE_KEY
from .sync.bundle import MANIFEST_HASH_CACHE_SECONDS
from .sync.bundle import build_bundle
from .sync.bundle import cached_manifest_hash

# Bulk downloads ask for explicit ranges; this caps one response.
SYNC_MAX_RANGE_BYTES = 256 * 1024 * 1024


def sync_token_required(view_func):
    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        header = request.headers.get("Authorization", "")
        raw = header[7:] if header.startswith("Bearer ") else ""
        instance = ICInstance.for_token(raw)
        if instance is None:
            return HttpResponse(
                "Invalid or missing IC sync token.",
                status=401,
                content_type="text/plain",
            )
        request.ic_instance = instance
        ICInstance.objects.filter(pk=instance.pk).update(last_seen_at=timezone.now())
        return view_func(request, *args, **kwargs)

    _wrapped.permission_check = "ic_sync_token: ICInstance bearer token"
    return csrf_exempt(login_not_required(_wrapped))


def _syncable_resource_ids():
    from .models import ICProgram

    return set(
        ICProgramItem.objects.filter(program__in=ICProgram.syncable()).values_list(
            "content__resource_id", flat=True
        )
    )


@require_GET
@sync_token_required
def manifest(request):
    manifest_data, _, digest = build_bundle()
    cache.set(MANIFEST_HASH_CACHE_KEY, digest, MANIFEST_HASH_CACHE_SECONDS)
    response = JsonResponse({**manifest_data, "manifest_hash": digest})
    response["Cache-Control"] = "no-store"
    return response


@require_GET
@sync_token_required
def check(request):
    """The booth's frequent check: has production changed since it last synced?"""
    return JsonResponse({"manifest_hash": cached_manifest_hash()})


@require_GET
@sync_token_required
def fixture(request):
    _, fixture_bytes, _ = build_bundle()
    response = HttpResponse(fixture_bytes, content_type="application/json")
    response["Cache-Control"] = "no-store"
    return response


@require_GET
@sync_token_required
def projection_file(request, resource_file_id):
    master = get_object_or_404(
        ResourceFile, pk=resource_file_id, is_projection_file=True
    )
    if master.resource_id not in _syncable_resource_ids():
        return HttpResponse(
            "This master isn't part of any syncable program.", status=403
        )
    if master.master_released_at or not master.bytes_on_this_server:
        raise Http404("Production no longer holds this master.")
    return _file_response(request, master, max_range_bytes=SYNC_MAX_RANGE_BYTES)


@require_GET
@sync_token_required
def subtitle_file(request, subtitle_id):
    subtitle = get_object_or_404(Subtitle, pk=subtitle_id)
    if subtitle.resource_id not in _syncable_resource_ids():
        return HttpResponse(
            "This subtitle isn't part of any syncable program.", status=403
        )
    with subtitle.subtitles_file.open("rb") as handle:
        data = handle.read()
    return HttpResponse(data, content_type="text/vtt; charset=utf-8")


@require_POST
@sync_token_required
def status(request):
    """A booth's run report: applied state, verified files, and the run's outcome."""
    try:
        report = json.loads(request.body or b"{}")
    except ValueError:
        return HttpResponseBadRequest("Expected a JSON report.")
    instance = request.ic_instance
    now = timezone.now()
    release_candidates = []
    with transaction.atomic():
        instance.last_status = report
        instance.last_seen_at = now
        if report.get("applied_manifest_hash"):
            instance.last_applied_manifest_hash = report["applied_manifest_hash"]
        instance.save(
            update_fields=["last_status", "last_seen_at", "last_applied_manifest_hash"]
        )

        for entry in report.get("verified_files", []):
            if entry.get("kind") != "projection":
                continue
            master = ResourceFile.objects.filter(
                pk=entry.get("resource_file_id"), is_projection_file=True
            ).first()
            if master is None:
                continue
            copy, _ = ICInstanceFile.objects.get_or_create(
                instance=instance,
                resource_file=master,
                defaults={"verified_at": now},
            )
            copy.xxh64 = entry.get("xxh64", "")
            copy.verified_at = now
            # Once a fresh hash has been reported it stays true until a mismatch.
            copy.hashed_from_bytes = (
                copy.hashed_from_bytes or bool(entry.get("hashed_this_run"))
            ) and copy.xxh64 == master.checksum
            copy.save()
            release_candidates.append(master)

        for entry in report.get("lost_files", []):
            ICInstanceFile.objects.filter(
                instance=instance, resource_file_id=entry.get("resource_file_id")
            ).delete()

    for master in release_candidates:
        release_if_ready(master)
    return JsonResponse({"ok": True})
