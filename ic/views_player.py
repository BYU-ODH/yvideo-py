"""The IC player: an operator control page and a patron-facing output window.

Production serves these to IC editors as a preview, streaming the SD editing file. The
booth serves the same pages (via ic.views_booth) to its userless kiosk, playing the local
projection master. Both build their pages and player data with the functions here, so
the preview is exactly what the booth shows apart from the video file.
"""

from django.http import Http404
from django.http import JsonResponse
from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET

from core.views import _file_response

from . import readiness
from .models import ICProgram
from .permissions import ic_editor_required
from .permissions import ic_item_preview_required
from .permissions import ic_program_write_required
from .sync.bundle import content_fingerprint


def item_json(item, *, playable=True, problems=()):
    content = item.content
    return {
        "id": item.pk,
        "position": item.position,
        "title": content.title,
        "mode": item.mode,
        "auto_advance": item.auto_advance,
        "subtitles": item.subtitles,
        "subtitle_track_id": item.content.default_subtitle_track_id
        if item.subtitles == item.Subtitles.VTT
        else None,
        # The media URL comes with the player data, so the annotations and the file
        # they're timed against are always fetched as one pair.
        "player_data_url": reverse("ic_item_player_data", args=[item.pk]),
        "playable": playable,
        "problems": [{"severity": p.severity, "message": p.message} for p in problems],
    }


def control_context(request, program, *, items, booth):
    return {
        "program": program,
        "items": items,
        "page_data": {
            "program": {
                "id": program.pk,
                "name": program.name,
                "start_date": program.start_date.isoformat(),
                "end_date": program.end_date.isoformat(),
            },
            "items": items,
            "output_url": reverse("ic_output"),
            "booth": booth,
        },
        "booth": booth,
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
        "fingerprint": content_fingerprint(content),
        "media_url": media_url,
    }


def programs_for_index(on=None):
    on = on or timezone.localdate()
    programs = list(ICProgram.syncable(on))
    return [p for p in programs if p.is_active(on)], [
        p for p in programs if p.is_upcoming(on)
    ]


# ---------------------------------------------------------------------------
# Production preview views (IC editors)
# ---------------------------------------------------------------------------


@require_GET
@ic_editor_required
def control_index(request):
    active, upcoming = programs_for_index()
    rows = [
        {
            "program": program,
            "status": readiness.rollup(readiness.program_problems(program)),
            "active": program in active,
        }
        for program in active + upcoming
    ]
    return render(
        request,
        "ic/control_index.html",
        {"rows": rows, "booth": False, "title": "International Cinema preview"},
    )


@require_GET
@ic_program_write_required
def control(request, program):
    items = []
    for item in program.ordered_items():
        problems = readiness.item_problems(item)
        has_media = bool(item.content.resource_file_id)
        items.append(item_json(item, playable=has_media, problems=problems))
    context = control_context(request, program, items=items, booth=False)
    return render(request, "ic/control.html", context)


@require_GET
@ic_editor_required
def output(request):
    return render(request, "ic/output.html", {"booth": False})


@require_GET
@ic_item_preview_required
def item_player_data(request, item):
    return JsonResponse(
        player_data_for_item(item, reverse("ic_item_media", args=[item.pk]))
    )


@require_GET
@ic_item_preview_required
def item_media(request, item):
    """The preview streams the SD editing file, never the master."""
    resource_file = item.content.resource_file
    if resource_file is None or not resource_file.bytes_on_this_server:
        raise Http404("This content has no playable file on this server.")
    return _file_response(request, resource_file)
