"""What production sends the booth: the bundle.

The booth plays exactly what's here: each scheduled item's player data, rendered by
Content.get_player_json() on production, plus what the controller shows and the
masters to fetch. The booth never renders player data itself.
"""

from dataclasses import asdict
import hashlib
import json
import time

import xxhash

from ic import readiness
from ic.models import ICProgram

# Bump when the player data's shape changes, so a booth whose AnnotationPlayer.js
# predates it keeps its previous bundle until it's redeployed.
BUNDLE_FORMAT = 1

# Readiness the projectionist can't act on, or that the booth decides for itself.
PRODUCTION_ONLY = {
    "not_ic_playlist",
    "timing_unverified",
    "transcode_pending",
    "transcode_failed",
    "master_lost",
    "not_on_booth",
}


def manifest_hash(bundle):
    canonical = json.dumps(
        bundle, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def file_xxh64(
    path, chunk_size=8 * 1024 * 1024, max_bytes_per_second=None, on_bytes=None
):
    """xxh64 of a file. max_bytes_per_second caps the read rate (the booth hashes from
    the disk the projector is playing from); on_bytes is called after each chunk."""
    digest = xxhash.xxh64()
    started = time.monotonic()
    read = 0
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(chunk_size), b""):
            digest.update(chunk)
            if on_bytes:
                on_bytes(len(chunk))
            if max_bytes_per_second:
                read += len(chunk)
                ahead = read / max_bytes_per_second - (time.monotonic() - started)
                if ahead > 0:
                    time.sleep(ahead)
    return digest.hexdigest()


def player_data(item):
    """The AnnotationPlayer payload for one item, subtitles chosen by its setting."""
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
        "annotations": player_json["annotations"],
        "subtitleTracks": subtitle_tracks,
    }


def program_entry(program):
    """A program as the controller shows it, with each item's booth-relevant problems."""
    items = []
    for item in program.ordered_items():
        master = item.projection_file()
        items.append(
            {
                "id": item.pk,
                "title": item.content.title,
                "mode": item.mode,
                "auto_advance": item.auto_advance,
                "subtitles": item.subtitles,
                "master": master
                and {"resource_file_id": master.pk, "xxh64": master.checksum},
                "problems": [
                    asdict(p)
                    for p in readiness.item_problems(item)
                    if p.code not in PRODUCTION_ONLY
                ],
            }
        )
    return {
        "id": program.pk,
        "name": program.name,
        "start_date": program.start_date.isoformat(),
        "end_date": program.end_date.isoformat(),
        "items": items,
    }


def build_bundle(on=None):
    """(bundle, its hash) for the current syncable programs."""
    programs = list(ICProgram.syncable(on).order_by("pk"))
    players, masters = {}, {}
    for program in programs:
        for item in program.ordered_items():
            players[str(item.pk)] = player_data(item)
            master = item.projection_file()
            if master is None:
                continue
            first, last = masters.get(master.pk, (master, None, None))[1:]
            masters[master.pk] = (
                master,
                min(first or program.start_date, program.start_date),
                max(last or program.end_date, program.end_date),
            )
    bundle = {
        "format": BUNDLE_FORMAT,
        "programs": [program_entry(p) for p in programs],
        "player_data": players,
        "files": [
            {
                "resource_file_id": master.pk,
                "first_program_start": first.isoformat(),
                "last_program_end": last.isoformat(),
                "name": master.file.name,
                "size": master.size_bytes,
                "xxh64": master.checksum,
                "available_from_parent": master.master_released_at is None
                and master.bytes_on_this_server,
            }
            for master, first, last in (masters[pk] for pk in sorted(masters))
        ],
    }
    return bundle, manifest_hash(bundle)


MANIFEST_HASH_CACHE_KEY = "ic_manifest_hash"
MANIFEST_HASH_CACHE_SECONDS = 15


def cached_manifest_hash():
    """The manifest hash, recomputed at most every few seconds.

    For requests that repeat (each booth's check every CHECK_INTERVAL_SECONDS, and
    the IC dashboard); building the bundle means rendering every scheduled film's
    player JSON. A change shows up at most this many seconds late.
    """
    from django.core.cache import cache

    digest = cache.get(MANIFEST_HASH_CACHE_KEY)
    if digest is None:
        digest = build_bundle()[1]
        cache.set(MANIFEST_HASH_CACHE_KEY, digest, MANIFEST_HASH_CACHE_SECONDS)
    return digest
