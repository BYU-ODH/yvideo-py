"""Helpers for detecting and provisioning YouTube-backed Content."""

import re
from urllib.parse import parse_qs
from urllib.parse import urlparse

from .models import Resource

_ID_RE = re.compile(r"^[\w-]{11}$")


def parse_youtube_video_id(url):
    """Extract a YouTube video id from a URL, or return None if unrecognized.

    Handles ``watch?v=<id>``, ``youtu.be/<id>``, ``/shorts/<id>``, and
    ``/embed/<id>``, ignoring extraneous query params (timestamps, share
    tracking, etc).
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
            match = re.match(r"^/(?:shorts|embed)/([^/]+)", parsed.path)
            if match:
                video_id = match.group(1)

    if video_id and _ID_RE.fullmatch(video_id):
        return video_id
    return None


def get_or_create_youtube_resource(video_id, requester_username):
    """Get-or-create the per-video Resource that backs a YouTube Content.

    Deduped by ``imdb_id`` (not ``name``, which is just a human-facing label an
    admin could rename, and not by Content row), so the same video reused
    across multiple playlists shares one Resource - and therefore one
    annotation-set pool - while different videos never share.
    """
    resource, _ = Resource.objects.get_or_create(
        imdb_id=f"YT{video_id}",
        defaults={
            "name": f"YouTube: {video_id}",
            "media_type": Resource.MediaType.WEB,
            "requester_username": requester_username,
        },
    )
    return resource
