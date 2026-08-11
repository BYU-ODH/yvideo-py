"""Helpers for detecting and provisioning YouTube-backed Content."""

import re
from urllib.parse import parse_qs
from urllib.parse import urlparse

from .models import YOUTUBE_VIDEO_ID_PATTERN
from .models import Resource

_ID_RE = re.compile(YOUTUBE_VIDEO_ID_PATTERN)

# The prefix a YouTube-backed Resource's imdb_id carries, ahead of the video id itself.
RESOURCE_ID_PREFIX = "YT"


def parse_youtube_video_id(url):
    """Extract a YouTube video id from a URL, or return None if unrecognized.

    Handles ``watch?v=<id>``, ``youtu.be/<id>``, and ``/embed/<id>``, ignoring extraneous
    query params (timestamps, share tracking, etc).

    ``/shorts/<id>`` is deliberately *not* accepted. A Short is vertical, and the player
    reports a constant 16:9 as its intrinsic size (the IFrame API exposes no real
    resolution - see YouTubeVideoElement.videoWidth), which is what AnnotationPlayer feeds
    to contentRect() to decide where the picture sits inside the element box. For a 9:16
    video that computed frame is roughly three times too wide, so every blur would be
    stored and drawn against a rectangle the video does not occupy. Refusing the URL is
    honest; silently misplacing blurs is not. Accepting Shorts means carrying a real aspect
    ratio through to the element first.
    """
    if not url:
        return None

    parsed = urlparse(url)
    host = parsed.netloc.lower()
    if host.startswith("www."):
        host = host[len("www.") :]
    elif host.startswith("m."):
        host = host[len("m.") :]

    video_id = None
    if host == "youtu.be":
        video_id = parsed.path.lstrip("/").split("/")[0]
    elif host in ("youtube.com", "youtube-nocookie.com"):
        if parsed.path == "/watch":
            video_id = parse_qs(parsed.query).get("v", [None])[0]
        else:
            match = re.match(r"^/embed/([^/]+)", parsed.path)
            if match:
                video_id = match.group(1)

    if video_id and _ID_RE.fullmatch(video_id):
        return video_id
    return None


def youtube_video_id_for_resource(resource):
    """The video id a Resource is the YouTube-backed record for, or None if it isn't one."""
    imdb_id = (resource.imdb_id if resource else None) or ""
    if not imdb_id.startswith(RESOURCE_ID_PREFIX):
        return None
    video_id = imdb_id[len(RESOURCE_ID_PREFIX) :]
    return video_id if _ID_RE.fullmatch(video_id) else None


def youtube_video_id_for_content(content, source_url):
    """The video to embed for `content`, or None if it isn't YouTube-backed.

    `source_url` must be the caller's already permission-checked source URL
    (``User.get_content_source_url``), and a falsy one short-circuits to None: the Resource
    is readable without any such check, so consulting it first would hand the video id to
    users who are not allowed to view the content.

    Past that gate the Resource wins over the URL. Annotations belong to the Resource's
    annotation sets, so if the two ever disagree - a Content's URL edited after creation,
    which keeps the original Resource - the Resource names the video those annotations were
    actually authored against. Falling back to the URL covers Content created by any path
    that didn't go through get_or_create_youtube_resource.
    """
    if not source_url:
        return None
    return youtube_video_id_for_resource(
        content.get_resource()
    ) or parse_youtube_video_id(source_url)


def get_or_create_youtube_resource(video_id, requester_username):
    """Get-or-create the per-video Resource that backs a YouTube Content.

    Deduped by ``imdb_id`` (not ``name``, which is just a human-facing label an
    admin could rename, and not by Content row), so the same video reused
    across multiple playlists shares one Resource - and therefore one
    annotation-set pool - while different videos never share.
    """
    resource, _ = Resource.objects.get_or_create(
        imdb_id=f"{RESOURCE_ID_PREFIX}{video_id}",
        defaults={
            "name": f"YouTube: {video_id}",
            "media_type": Resource.MediaType.WEB,
            "requester_username": requester_username,
        },
    )
    return resource
