"""The IC player: an operator control page and a patron-facing output window.

Production serves these pages to IC editors as a preview that streams the SD editing
file. The booth (IS_IC_INSTANCE) serves the same pages to its userless kiosk, playing
the local projection master, plus its sync status views. The preview is exactly what
the booth shows apart from the video file.
"""

from functools import wraps

from django.conf import settings
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
from core.permissions import forbidden
from core.views import _file_response

from . import readiness
from .models import ICBoothState
from .models import ICLocalFile
from .models import ICProgram
from .models import ICProgramItem
from .models import ICSyncRun
from .permissions import ic_editor_required
from .sync.agent import disk_usage


def player_view(view_func):
    """IC editors on production. On the booth, the kiosk: it has no users at all (and no
    LoginRequiredMiddleware), and is bound to localhost."""

    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        if not settings.IS_IC_INSTANCE and not request.user.is_ic_editor:
            return forbidden("Only International Cinema editors can do that.")
        return view_func(request, *args, **kwargs)

    _wrapped.permission_check = "ic_player: IC editors, or the userless booth kiosk"
    return _wrapped


def booth_view(view_func):
    """Only on the booth; production has no such page."""

    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        if not settings.IS_IC_INSTANCE:
            raise Http404()
        return view_func(request, *args, **kwargs)

    _wrapped.permission_check = "booth: userless kiosk bound to localhost"
    return _wrapped


def _render(request, template, context):
    return render(request, template, {"booth": settings.IS_IC_INSTANCE, **context})


def _master_on_disk(item):
    """On the booth: the item's master, if a verified copy is on this disk."""
    master = item.projection_file()
    if master is None:
        return None
    local = ICLocalFile.objects.filter(resource_file_id=master.pk).first()
    if local is None or local.xxh64 != master.checksum:
        return None
    if not master.file.storage.exists(master.file.name):
        return None
    return master


def item_problems(item):
    if not settings.IS_IC_INSTANCE:
        return readiness.item_problems(item)
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
            ),
        )
    return problems


def _is_playable(item, problems):
    if settings.IS_IC_INSTANCE:
        return not any(p.code == "not_on_booth" for p in problems)
    return bool(item.content.resource_file_id)


def item_json(item, problems):
    content = item.content
    return {
        "id": item.pk,
        "title": content.title,
        "mode": item.mode,
        "auto_advance": item.auto_advance,
        "subtitles": item.subtitles,
        # The media URL comes with the player data, so the annotations and the file
        # they're timed against are always fetched as one pair.
        "player_data_url": reverse("ic_item_player_data", args=[item.pk]),
        "playable": _is_playable(item, problems),
        "problems": [{"severity": p.severity, "message": p.message} for p in problems],
    }


def player_data_for_item(item, media_url):
    """The AnnotationPlayer payload for one item, subtitles chosen by the item's setting.

    media_url is the video to play with these annotations. The output keeps both for as
    long as the item is loaded, so nothing a sync does can change a film mid-showing.
    """
    content = item.content
    player_json = content.get_player_json(include_subtitles=True)
    subtitle_tracks = []
    if item.subtitles == item.Subtitles.VTT and content.default_subtitle_track_id:
        subtitle_tracks = [
            {**track, "default": True}
            for track in player_json["subtitleTracks"]
            if track["id"] == content.default_subtitle_track_id
        ]
    return {
        "item_id": item.pk,
        "title": content.title,
        "mode": item.mode,
        "annotations": player_json["annotations"],
        "subtitleTracks": subtitle_tracks,
        "media_url": media_url,
    }


def _data_version(state):
    return state.applied_at.isoformat() if state.applied_at else ""


def _sync_summary():
    """The booth's sync indicators. Playback never depends on production."""
    state = ICBoothState.get()
    return {
        "state": state,
        "data_version": _data_version(state),
        "latest": ICSyncRun.objects.order_by("-requested_at").first(),
        "last_run": ICSyncRun.latest_finished(),
        "last_success": ICSyncRun.latest_finished(status=ICSyncRun.Status.SUCCEEDED),
        "parent_unreachable": bool(state.parent_check_error),
    }


# ---------------------------------------------------------------------------
# The player, on both instances
# ---------------------------------------------------------------------------


@require_GET
@player_view
def control_index(request):
    today = timezone.localdate()
    rows = []
    # Yesterday's program counts as showing until the end of the night (late shows).
    for program in ICProgram.syncable(today):
        items = list(program.ordered_items())
        problems = [p for item in items for p in item_problems(item)]
        rows.append(
            {
                "program": program,
                "status": readiness.rollup(
                    problems if items else readiness.program_problems(program)
                ),
                "active": not program.is_upcoming(today),
            }
        )
    context = {"rows": rows, "title": "International Cinema"}
    if settings.IS_IC_INSTANCE:
        context.update(_sync_summary())
    return _render(request, "ic/control_index.html", context)


@require_GET
@player_view
def control(request, program_id):
    program = get_object_or_404(ICProgram, pk=program_id)
    booth = settings.IS_IC_INSTANCE
    if booth and not program.is_syncable():
        raise Http404("This program has ended.")
    items = [item_json(item, item_problems(item)) for item in program.ordered_items()]
    page_data = {
        "program": {"id": program.pk, "name": program.name},
        "items": items,
        "output_url": reverse("ic_output"),
        "booth": booth,
    }
    context = {"program": program, "items": items, "page_data": page_data}
    if booth:
        context.update(_sync_summary())
        # When the sync badge reports a newer apply, the controller asks for a reload:
        # its queue was built from the data applied before.
        page_data["data_version"] = context["data_version"]
        context["rehearsal"] = program.is_upcoming()
    return _render(request, "ic/control.html", context)


@require_GET
@player_view
def output(request):
    return _render(request, "ic/output.html", {})


@require_GET
@player_view
def item_player_data(request, item_id):
    item = get_object_or_404(
        ICProgramItem.objects.select_related("program", "content"), pk=item_id
    )
    if not settings.IS_IC_INSTANCE:
        media_url = reverse("ic_item_media", args=[item.pk])
    else:
        if not item.program.is_syncable():
            raise Http404("This program has ended.")
        master = _master_on_disk(item)
        if master is None:
            raise Http404("The projection master isn't on this booth.")
        media_url = reverse("ic_local_file", args=[master.pk, master.checksum])
    return JsonResponse(player_data_for_item(item, media_url))


@require_GET
@ic_editor_required
def item_media(request, item_id):
    """Production's preview streams the SD editing file, never the master."""
    item = get_object_or_404(ICProgramItem, pk=item_id)
    resource_file = item.content.resource_file
    if resource_file is None or not resource_file.bytes_on_this_server:
        raise Http404("This content has no playable file on this server.")
    return _file_response(request, resource_file)


# ---------------------------------------------------------------------------
# Booth only: local files and sync status
# ---------------------------------------------------------------------------


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
        resource_file_id=resource_file_id,
        xxh64=xxh64,
    )
    # Resolved through ResourceFile's storage, which keeps masters outside MEDIA_ROOT.
    file_obj = ResourceFile(file=local.name)
    if not file_obj.file.storage.exists(local.name):
        raise Http404("The projection master isn't on this booth.")
    response = _file_response(request, file_obj)
    response["Cache-Control"] = "private, max-age=31536000, immutable"
    return response


@require_GET
@booth_view
def status(request):
    return _render(
        request,
        "ic/booth_status.html",
        {
            "title": "Sync status",
            "runs": ICSyncRun.objects.order_by("-requested_at")[:25],
            "local_files": ICLocalFile.objects.order_by("resource_file_id"),
            "disk": disk_usage(),
            **_sync_summary(),
        },
    )


@require_GET
@booth_view
def sync_badge(request):
    return _render(request, "ic/partials/sync_badge.html", _sync_summary())


@require_POST
@booth_view
def sync_run_create(request):
    """Force pull: queue a sync run for the agent and return its live indicator."""
    if not ICBoothState.get().agent_alive:
        return HttpResponse(
            "Sync agent isn't running; restart it with "
            "`systemctl --user restart ic-sync-agent`.",
            status=503,
            content_type="text/plain",
        )
    # A press while a run is still queued joins it: every run does the same work.
    run = (
        ICSyncRun.objects.filter(status=ICSyncRun.Status.QUEUED)
        .order_by("requested_at")
        .first()
    ) or ICSyncRun.objects.create()
    return _render(request, "ic/partials/sync_run.html", {"run": run})


@require_GET
@booth_view
def sync_run_detail(request, run_id):
    run = get_object_or_404(ICSyncRun, pk=run_id)
    return _render(request, "ic/partials/sync_run.html", {"run": run})
