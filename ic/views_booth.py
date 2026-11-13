"""The booth kiosk: userless, bound to localhost, read-only except "Sync now".

Mounted by yvideo.urls_ic only. Every view is login-exempt (the booth has no users) and
refuses to run on production, so a mis-mounted route cannot expose it.
"""

from functools import wraps
import shutil

from django.conf import settings
from django.contrib.auth.decorators import login_not_required
from django.http import Http404
from django.http import HttpResponse
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET
from django.views.decorators.http import require_POST

from core.models import ResourceFile
from core.views import _file_response

from . import booth_state
from . import readiness
from .models import ICBoothState
from .models import ICLocalFile
from .models import ICProgram
from .models import ICProgramItem
from .models import ICSyncRun
from .views_player import control_context
from .views_player import item_json
from .views_player import player_data_for_item
from .views_player import programs_for_index


def booth_view(view_func):
    """Login-exempt, and only on an IC instance."""

    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        if not settings.IS_IC_INSTANCE:
            raise Http404()
        return view_func(request, *args, **kwargs)

    _wrapped.permission_check = "booth: userless kiosk bound to localhost"
    return login_not_required(_wrapped)


def _playable_program(program, on=None):
    """Active programs play; upcoming ones play as rehearsals; past ones never do."""
    return not program.is_past(on)


def _master_on_disk(item):
    master = item.projection_file()
    if master is None:
        return None
    local = ICLocalFile.objects.filter(
        kind=ICLocalFile.Kind.PROJECTION, ref_id=master.pk
    ).first()
    if local is None or local.xxh64 != master.checksum:
        return None
    if not master.file.storage.exists(master.file.name):
        return None
    return master


def booth_item_problems(item):
    problems = [
        p
        for p in readiness.item_problems(item)
        # Production-side concerns the booth cannot act on.
        if p.code not in ("timing_unverified", "not_ic_playlist")
    ]
    if _master_on_disk(item) is None:
        problems.insert(
            0,
            readiness.Problem(
                readiness.ERROR,
                "not_on_booth",
                "The projection master isn't on this booth yet (see Sync status).",
                item.pk,
            ),
        )
    return problems


def _data_version(state):
    return state.applied_at.isoformat() if state.applied_at else ""


def _sync_summary():
    state = ICBoothState.get()
    latest = ICSyncRun.objects.order_by("-requested_at").first()
    return {
        "state": state,
        "data_version": _data_version(state),
        "latest": latest,
        "agent_alive": booth_state.agent_alive(),
        # The agent's last check failed. Playback never depends on production.
        "parent_unreachable": bool(state.parent_check_error),
    }


@require_GET
@booth_view
def control_index(request):
    active, upcoming = programs_for_index()
    rows = []
    for program in active + upcoming:
        problems = []
        for item in program.ordered_items():
            problems.extend(booth_item_problems(item))
        rows.append(
            {
                "program": program,
                "status": readiness.rollup(problems),
                "active": program in active,
            }
        )
    return render(
        request,
        "ic/control_index.html",
        {
            "rows": rows,
            "booth": True,
            "title": "International Cinema",
            **_sync_summary(),
        },
    )


@require_GET
@booth_view
def control(request, program_id):
    program = get_object_or_404(ICProgram, pk=program_id)
    if not _playable_program(program):
        raise Http404("This program has ended.")
    items = []
    for item in program.ordered_items():
        problems = booth_item_problems(item)
        items.append(
            item_json(
                item,
                playable=not any(p.code == "not_on_booth" for p in problems),
                problems=problems,
            )
        )
    context = control_context(request, program, items=items, booth=True)
    context.update(_sync_summary())
    # When the sync badge reports a newer apply, the controller asks for a reload: its
    # queue was built from the data applied before.
    context["page_data"]["data_version"] = _data_version(context["state"])
    context["rehearsal"] = program.is_upcoming()
    return render(request, "ic/control.html", context)


@require_GET
@booth_view
def output(request):
    return render(request, "ic/output.html", {"booth": True})


def _item_or_404(item_id):
    item = get_object_or_404(
        ICProgramItem.objects.select_related("program", "content"), pk=item_id
    )
    if not _playable_program(item.program):
        raise Http404("This program has ended.")
    return item


@require_GET
@booth_view
def item_player_data(request, item_id):
    item = _item_or_404(item_id)
    master = _master_on_disk(item)
    if master is None:
        raise Http404("The projection master isn't on this booth.")
    return JsonResponse(
        player_data_for_item(
            item, reverse("ic_local_file", args=[master.pk, master.checksum])
        )
    )


@require_GET
@booth_view
def local_file(request, resource_file_id, xxh64):
    """One verified projection master, addressed by id and checksum.

    The bytes behind a URL never change: a replaced master has a new checksum and so a
    new URL, and its predecessor stays on disk through prune's grace period. That is why
    a sync can apply at any time without touching the film that's playing.
    """
    local = get_object_or_404(
        ICLocalFile,
        kind=ICLocalFile.Kind.PROJECTION,
        ref_id=resource_file_id,
        xxh64=xxh64,
    )
    # Resolved through ResourceFile's storage, which keeps masters outside MEDIA_ROOT.
    file_obj = ResourceFile(file=local.name)
    if not file_obj.file.storage.exists(local.name):
        raise Http404("The projection master isn't on this booth.")
    response = _file_response(request, file_obj)
    response["Cache-Control"] = "private, max-age=31536000, immutable"
    return response


def _disk_free():
    try:
        usage = shutil.disk_usage(settings.IC_PROJECTION_ROOT)
    except OSError:
        return None
    return {"free": usage.free, "total": usage.total}


@require_GET
@booth_view
def status(request):
    return render(
        request,
        "ic/booth_status.html",
        {
            "booth": True,
            "title": "Sync status",
            "runs": ICSyncRun.objects.order_by("-requested_at")[:25],
            "local_files": ICLocalFile.objects.order_by("kind", "ref_id"),
            "disk": _disk_free(),
            "agent": booth_state.agent_heartbeat(),
            **_sync_summary(),
        },
    )


@require_GET
@booth_view
def sync_badge(request):
    return render(request, "ic/partials/sync_badge.html", _sync_summary())


@require_POST
@booth_view
def sync_run_create(request):
    """Force pull: queue a sync run for the agent and return its live indicator."""
    if not booth_state.agent_alive():
        return HttpResponse(
            booth_state.AGENT_DOWN_MESSAGE, status=503, content_type="text/plain"
        )
    # A second press while one is still queued joins it.
    run = (
        ICSyncRun.objects.filter(
            status=ICSyncRun.Status.QUEUED, trigger=ICSyncRun.Trigger.FORCE_PULL
        )
        .order_by("requested_at")
        .first()
    ) or ICSyncRun.objects.create(trigger=ICSyncRun.Trigger.FORCE_PULL)
    return render(request, "ic/partials/sync_run.html", {"run": run, "booth": True})


@require_GET
@booth_view
def sync_run_detail(request, run_id):
    run = get_object_or_404(ICSyncRun, pk=run_id)
    if "application/json" in request.headers.get("Accept", ""):
        return JsonResponse(run.progress_json())
    behind = (
        ICSyncRun.objects.filter(
            status=ICSyncRun.Status.RUNNING, requested_at__lt=run.requested_at
        ).first()
        if run.status == ICSyncRun.Status.QUEUED
        else None
    )
    return render(
        request,
        "ic/partials/sync_run.html",
        {"run": run, "behind": behind, "booth": True, "now": timezone.now()},
    )


@require_GET
@booth_view
def static_file(request, path):
    """The kiosk's static files, from collectstatic's output.

    The booth runs no Apache in front of gunicorn; it serves one local browser, so
    Django serving STATIC_ROOT is enough (and keeps the booth fully offline).
    """
    from django.views.static import serve

    return serve(request, path, document_root=settings.STATIC_ROOT)
