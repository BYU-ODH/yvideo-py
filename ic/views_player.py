"""The IC player: an operator control page and a patron-facing output window.

Production serves these pages to IC editors as a preview that streams the SD editing
file. The booth (IS_IC_INSTANCE) serves the same pages to its userless kiosk, playing
the local projection master, plus its sync status views. The preview is exactly what
the booth shows apart from the video file.
"""

from datetime import date
from functools import wraps
import json

from django.conf import settings
from django.http import Http404
from django.http import HttpResponse
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.shortcuts import render
from django.urls import reverse
from django.views.decorators.http import require_GET
from django.views.decorators.http import require_POST

from core.models import ResourceFile
from core.permissions import forbidden
from core.views import _file_response

from .models import ICBoothState
from .models import ICLocalFile
from .models import ICProgram
from .models import ICProgramItem
from .models import ICSyncRun
from .permissions import ic_editor_required
from .sync.agent import disk_usage
from .sync.bundle import manifest_hash
from .sync.bundle import player_data
from .sync.bundle import program_entry


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


def _program(entry):
    """A bundle program as an unsaved ICProgram, so its dates and helpers work."""
    program = ICProgram(
        id=entry["id"],
        name=entry["name"],
        start_date=date.fromisoformat(entry["start_date"]),
        end_date=date.fromisoformat(entry["end_date"]),
    )
    program.entries = entry["items"]
    return program


def _booth_programs(bundle):
    # The bundle can be days old if production is unreachable, so ended programs drop
    # out here, not only on production.
    programs = [_program(entry) for entry in bundle.get("programs", [])]
    return [program for program in programs if program.is_syncable()]


def _programs():
    """What the player offers: production's syncable programs, or the booth's bundle's."""
    if settings.IS_IC_INSTANCE:
        return _booth_programs(ICBoothState.get().bundle)
    return [_program(program_entry(p)) for p in ICProgram.syncable()]


def _get_program(program_id):
    if not settings.IS_IC_INSTANCE:
        # The preview shows any program, past ones included.
        return _program(program_entry(get_object_or_404(ICProgram, pk=program_id)))
    program = next((p for p in _programs() if p.pk == program_id), None)
    if program is None:
        raise Http404("This program isn't on the booth, or it has ended.")
    return program


def _local_master(entry):
    """On the booth: the verified copy of the item's master on this disk, or None."""
    local = (
        entry["master"] and ICLocalFile.objects.filter(xxh64=entry["master"]).first()
    )
    if local and ResourceFile(file=local.name).file.storage.exists(local.name):
        return local
    return None


def _playable(entry):
    """The booth plays only verified local masters; the preview needs an SD file.

    Readiness (codecs, subtitles, timing, ...) is for IC editors on production; the
    booth's controller shows only whether each item can play.
    """
    if settings.IS_IC_INSTANCE:
        return _local_master(entry) is not None
    return ICProgramItem.objects.filter(
        pk=entry["id"], content__resource_file__isnull=False
    ).exists()


def item_json(entry):
    return {
        "id": entry["id"],
        "title": entry["title"],
        "mode": entry["mode"],
        "auto_advance": entry["auto_advance"],
        "subtitles": entry["subtitles"],
        # The media URL comes with the player data, so the annotations and the file
        # they're timed against are always fetched as one pair.
        "player_data_url": reverse("ic_item_player_data", args=[entry["id"]]),
        "playable": _playable(entry),
    }


def _data_versions(state):
    """Each program's version, as its controller page was built from it: when it
    changes, that page is stale. Player data is fetched fresh for every item, and a
    change to another program, or to which masters production holds, isn't news."""
    return {p["id"]: manifest_hash(p) for p in state.bundle.get("programs", [])}


def _sync_summary():
    """The booth's sync indicators. Playback never depends on production."""
    state = ICBoothState.get()
    return {
        "state": state,
        "data_versions_json": json.dumps(_data_versions(state)),
        "latest": ICSyncRun.objects.order_by("-requested_at").first(),
        "last_run": ICSyncRun.latest_finished(),
        "parent_unreachable": bool(state.parent_check_error),
    }


# ---------------------------------------------------------------------------
# The player, on both instances
# ---------------------------------------------------------------------------


@require_GET
@player_view
def control_index(request):
    rows = []
    # Yesterday's program counts as showing until the end of the night (late shows).
    for program in _programs():
        # A program with nothing in it isn't ready.
        ready = bool(program.entries) and all(map(_playable, program.entries))
        rows.append(
            {
                "program": program,
                "status": "ready" if ready else "error",
                "active": not program.is_upcoming(),
            }
        )
    context = {"rows": rows, "title": "International Cinema"}
    if settings.IS_IC_INSTANCE:
        context.update(_sync_summary())
    return _render(request, "ic/control_index.html", context)


@require_GET
@player_view
def control(request, program_id):
    program = _get_program(program_id)
    booth = settings.IS_IC_INSTANCE
    items = [item_json(entry) for entry in program.entries]
    page_data = {
        "program": {"id": program.pk, "name": program.name},
        "items": items,
        "output_url": reverse("ic_output"),
        "booth": booth,
    }
    context = {"program": program, "items": items, "page_data": page_data}
    if booth:
        context.update(_sync_summary())
        # When the sync badge reports a newer version of this program, the controller
        # asks for a reload: its queue was built from the data applied before.
        page_data["data_version"] = _data_versions(ICBoothState.get()).get(program.pk)
        context["rehearsal"] = program.is_upcoming()
    return _render(request, "ic/control.html", context)


@require_GET
@player_view
def output(request):
    return _render(request, "ic/output.html", {})


@require_GET
@player_view
def item_player_data(request, item_id):
    """The item's player data and the media to play it with, as one pair.

    The output keeps both for as long as the item is loaded, so nothing a sync does can
    change a film mid-showing.
    """
    if not settings.IS_IC_INSTANCE:
        item = get_object_or_404(ICProgramItem, pk=item_id)
        media_url = reverse("ic_item_media", args=[item.pk])
        return JsonResponse({**player_data(item), "media_url": media_url})
    # One read of the bundle, so the player data and the master come from the same sync.
    bundle = ICBoothState.get().bundle
    entry = next(
        (
            entry
            for program in _booth_programs(bundle)
            for entry in program.entries
            if entry["id"] == item_id
        ),
        None,
    )
    local = entry and _local_master(entry)
    if not local:
        raise Http404("This item isn't playable on this booth.")
    media_url = reverse("ic_local_file", args=[local.xxh64])
    return JsonResponse({**bundle["player_data"][str(item_id)], "media_url": media_url})


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
def local_file(request, xxh64):
    """One verified projection master, addressed by its checksum.

    The bytes behind a URL never change: a replaced master has a new checksum and so a
    new URL, and its predecessor stays on disk through prune's grace period. That is why
    a sync can apply at any time without touching the film that's playing.
    """
    local = get_object_or_404(ICLocalFile, xxh64=xxh64)
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
            "local_files": ICLocalFile.objects.order_by("name"),
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
