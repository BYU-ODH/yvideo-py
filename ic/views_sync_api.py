"""Production's sync API, called by the booth with its bearer token.

Sync is pull-only. Besides fetching data, the booth checks in every
CHECK_INTERVAL_SECONDS (`check`), which is how it learns about changes, and reports
each run's outcome (`status`).
"""

from functools import wraps
import json
import secrets

from django.conf import settings
from django.contrib.auth.decorators import login_not_required
from django.core.cache import cache
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
from core.views import _file_response

from .jobs import release_if_ready
from .models import ICBoothReport
from .models import ICProgram
from .models import ICProgramItem
from .sync.bundle import MANIFEST_HASH_CACHE_KEY
from .sync.bundle import MANIFEST_HASH_CACHE_SECONDS
from .sync.bundle import build_bundle
from .sync.bundle import cached_manifest_hash


def sync_token_required(view_func):
    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        header = request.headers.get("Authorization", "")
        raw = header[7:] if header.startswith("Bearer ") else ""
        expected = settings.IC_SYNC_TOKEN
        if not (expected and secrets.compare_digest(raw, expected)):
            return HttpResponse(
                "Invalid or missing IC sync token.",
                status=401,
                content_type="text/plain",
            )
        return view_func(request, *args, **kwargs)

    _wrapped.permission_check = "ic_sync_token: settings.IC_SYNC_TOKEN bearer token"
    return csrf_exempt(login_not_required(_wrapped))


def _syncable_resource_ids():
    return set(
        ICProgramItem.objects.filter(program__in=ICProgram.syncable()).values_list(
            "content__resource_id", flat=True
        )
    )


@require_GET
@sync_token_required
def manifest(request):
    manifest_data, digest = build_bundle()
    cache.set(MANIFEST_HASH_CACHE_KEY, digest, MANIFEST_HASH_CACHE_SECONDS)
    response = JsonResponse({**manifest_data, "manifest_hash": digest})
    response["Cache-Control"] = "no-store"
    return response


@require_GET
@sync_token_required
def check(request):
    """The booth's frequent check: has production changed since it last synced?

    The agent checks every CHECK_INTERVAL_SECONDS, during runs too, so this (and the
    run report) is all "last contact" needs: no write per downloaded slice.
    """
    ICBoothReport.seen()
    return JsonResponse({"manifest_hash": cached_manifest_hash()})


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
    return _file_response(request, master)


@require_POST
@sync_token_required
def status(request):
    """The booth's run report: applied state, verified files, and the run's outcome.

    Production keeps the latest report; a master is released once it lists a copy whose
    checksum matches (see jobs.release_blockers).
    """
    try:
        report = json.loads(request.body or b"{}")
    except ValueError:
        return HttpResponseBadRequest("Expected a JSON report.")
    ICBoothReport.objects.update_or_create(
        pk=1, defaults={"report": report, "last_seen_at": timezone.now()}
    )
    for master in ResourceFile.objects.filter(
        is_projection_file=True,
        master_released_at__isnull=True,
        pk__in=[f.get("resource_file_id") for f in report.get("verified_files", [])],
    ):
        release_if_ready(master)
    return JsonResponse({"ok": True})
