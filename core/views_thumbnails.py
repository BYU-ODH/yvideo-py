import logging
import re

from django.http import Http404
from django.http import HttpResponse
from django.http import HttpResponseBadRequest
from django.shortcuts import render
from django.urls import reverse
from django.views.decorators.http import require_GET
from django.views.decorators.http import require_POST
import requests

from .models import Content
from .models import Thumbnail
from .permissions import content_read_required
from .permissions import content_write_required
from .permissions import playlist_read_required
from .permissions import playlist_write_required
from .youtube_utils import youtube_video_id_for_content

logger = logging.getLogger(__name__)

HEX_COLOR_RE = re.compile(r"#[0-9a-fA-F]{6}")
MAX_IMAGE_BYTES = 2 * 1024 * 1024
MAX_TEXT_LENGTH = 100
# The editor always uploads JPEGs, so anything else is not from our client.
JPEG_MAGIC = b"\xff\xd8\xff"


def _youtube_video_id(request, item):
    if not isinstance(item, Content):
        return None
    return youtube_video_id_for_content(item, request.user.get_content_source_url(item))


def _youtube_thumbnail_url(video_id):
    return f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"


def _fetch_youtube_thumbnail(video_id):
    response = requests.get(_youtube_thumbnail_url(video_id), timeout=10)
    response.raise_for_status()
    return response.content


def _render_editor(request, item, save_url):
    stream_url = None
    if isinstance(item, Content) and item.resource_file_id:
        key = request.user.get_resource_filekey(item)
        stream_url = reverse("stream_file", args=[key.pk]) if key else None
    video_id = _youtube_video_id(request, item)
    context = {
        "dialog_id": "thumbnail-editor-dialog",
        "thumbnail": Thumbnail.for_item(item),
        "save_url": save_url,
        "stream_url": stream_url,
        "youtube_image_url": _youtube_thumbnail_url(video_id) if video_id else None,
    }
    return render(request, "core/partials/modals/thumbnail_editor.html", context)


def _save(request, item):
    field = Thumbnail.field_for(item)
    thumbnail = getattr(item, "thumbnail", None) or Thumbnail(**{field: item})

    background_color = request.POST.get("background_color", "")
    text_color = request.POST.get("text_color", "")
    text = request.POST.get("text", "").strip()
    try:
        text_size = float(request.POST.get("text_size", ""))
    except ValueError:
        text_size = -1
    if not 0.75 <= text_size <= 4:
        return HttpResponseBadRequest("Invalid text size.")
    if not (
        HEX_COLOR_RE.fullmatch(background_color) and HEX_COLOR_RE.fullmatch(text_color)
    ):
        return HttpResponseBadRequest("Invalid color.")
    if len(text) > MAX_TEXT_LENGTH:
        return HttpResponseBadRequest(
            f"Text must be {MAX_TEXT_LENGTH} characters or fewer."
        )

    image_source = request.POST.get("image_source", "keep")
    if image_source == "upload":
        upload = request.FILES.get("image")
        if upload is None or upload.size > MAX_IMAGE_BYTES:
            return HttpResponseBadRequest("Image is missing or too large.")
        image = upload.read()
        if not image.startswith(JPEG_MAGIC):
            return HttpResponseBadRequest("Image must be a JPEG.")
        thumbnail.image = image
    elif image_source == "youtube":
        video_id = _youtube_video_id(request, item)
        if not video_id:
            return HttpResponseBadRequest("This video has no YouTube thumbnail.")
        try:
            thumbnail.image = _fetch_youtube_thumbnail(video_id)
        except requests.RequestException as e:
            logger.warning(f"Could not fetch YouTube thumbnail for {video_id}: {e}")
            return HttpResponse(
                "Could not get the YouTube thumbnail. Try again later.", status=502
            )
    elif image_source == "none":
        thumbnail.image = None
    elif image_source != "keep":
        return HttpResponseBadRequest("Invalid image source.")

    thumbnail.background_color = background_color
    thumbnail.text = {
        "color": text_color,
        "size": text_size,
    }
    # Unchanged default text is left unsaved so the tile keeps following renames.
    if text != thumbnail.default_text():
        thumbnail.text["text"] = text
    thumbnail.save()
    return HttpResponse(status=204)


def _image(item):
    thumbnail = getattr(item, "thumbnail", None)
    if thumbnail is None or thumbnail.image is None:
        raise Http404
    response = HttpResponse(bytes(thumbnail.image), content_type="image/jpeg")
    # Image URLs carry a version, so a stored copy never goes stale.
    response["Cache-Control"] = "private, max-age=31536000, immutable"
    return response


@require_GET
@playlist_write_required
def playlist_thumbnail_editor(request, playlist):
    return _render_editor(
        request, playlist, reverse("save_playlist_thumbnail", args=[playlist.pk])
    )


@require_POST
@playlist_write_required
def save_playlist_thumbnail(request, playlist):
    return _save(request, playlist)


@require_GET
@content_write_required
def content_thumbnail_editor(request, content):
    return _render_editor(
        request, content, reverse("save_content_thumbnail", args=[content.pk])
    )


@require_POST
@content_write_required
def save_content_thumbnail(request, content):
    return _save(request, content)


@require_GET
@playlist_read_required
def playlist_thumbnail_image(request, playlist):
    return _image(playlist)


@require_GET
@content_read_required
def content_thumbnail_image(request, content):
    return _image(content)
