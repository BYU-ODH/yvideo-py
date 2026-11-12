"""The booth's sync agent web surface: three signed routes and nothing else.

Mounted by yvideo.urls_ic_agent only, on the booth LAN port that the firewall opens to
production alone. A valid request can only start a pull from the real production
(IC_PARENT_URL), which is then verified, or read status.
"""

from functools import wraps
import json
import uuid

from django.conf import settings
from django.contrib.auth.decorators import login_not_required
from django.http import Http404
from django.http import HttpResponse
from django.http import HttpResponseBadRequest
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET
from django.views.decorators.http import require_POST

from . import booth_state
from .models import ICBoothState
from .models import ICSyncRun
from .sync.signing import SIGNATURE_HEADER
from .sync.signing import TIMESTAMP_HEADER
from .sync.signing import verify


def agent_signature_required(view_func):
    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        if not settings.IS_IC_INSTANCE:
            raise Http404()
        problem = verify(
            settings.IC_AGENT_SECRET,
            request.headers.get(TIMESTAMP_HEADER),
            request.headers.get(SIGNATURE_HEADER),
            request.method,
            request.path,
            request.body,
        )
        if problem:
            return HttpResponse(problem, status=403, content_type="text/plain")
        return view_func(request, *args, **kwargs)

    _wrapped.permission_check = "agent: HMAC-signed by production"
    return csrf_exempt(login_not_required(_wrapped))


TRIGGERS_FROM_PRODUCTION = {
    "event": ICSyncRun.Trigger.EVENT,
    "force_push": ICSyncRun.Trigger.FORCE_PUSH,
}


@require_POST
@agent_signature_required
def sync_trigger(request):
    try:
        body = json.loads(request.body or b"{}")
        request_id = uuid.UUID(body["request_id"])
        trigger = TRIGGERS_FROM_PRODUCTION[body.get("trigger", "force_push")]
    except (ValueError, KeyError, TypeError):
        return HttpResponseBadRequest("Expected JSON with request_id and trigger.")
    if not booth_state.agent_alive():
        return HttpResponse(
            booth_state.AGENT_DOWN_MESSAGE, status=503, content_type="text/plain"
        )

    existing = ICSyncRun.objects.filter(request_id=request_id).first()
    if existing is not None:
        return JsonResponse(
            {"run_id": str(existing.pk), "status": existing.status}, status=202
        )

    options = body.get("options") or {}
    options = {
        "full_rehash": bool(options.get("full_rehash")),
        "apply_during_playback": bool(options.get("apply_during_playback")),
    }
    if trigger == ICSyncRun.Trigger.EVENT:
        # One queued event run is enough; more would only repeat the same pull.
        queued_event = ICSyncRun.objects.filter(
            trigger=ICSyncRun.Trigger.EVENT, status=ICSyncRun.Status.QUEUED
        ).first()
        if queued_event is not None:
            return JsonResponse(
                {"run_id": str(queued_event.pk), "status": "queued"}, status=202
            )
    run = ICSyncRun.objects.create(
        trigger=trigger,
        request_id=request_id,
        requested_by=str(body.get("requested_by", ""))[:255],
        options=options,
    )
    return JsonResponse({"run_id": str(run.pk), "status": run.status}, status=202)


@require_GET
@agent_signature_required
def sync_run_progress(request, run_id):
    run = get_object_or_404(ICSyncRun, pk=run_id)
    data = run.progress_json()
    data["agent_alive"] = booth_state.agent_alive()
    data["playback_active"] = booth_state.playback_active()
    return JsonResponse(data)


@require_GET
@agent_signature_required
def agent_status(request):
    state = ICBoothState.get()
    return JsonResponse(
        {
            "agent_alive": booth_state.agent_alive(),
            "playback_active": booth_state.playback_active(),
            "applied_manifest_hash": state.applied_manifest_hash,
            "last_success_at": state.last_success_at.isoformat()
            if state.last_success_at
            else None,
            "last_run_status": state.last_run_status,
            "last_run_error": state.last_run_error,
        }
    )
