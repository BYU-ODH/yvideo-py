"""What production sends the booth: the bundle.

The booth plays exactly what's here: each scheduled item's player data, rendered by
Content.get_player_json() on production, plus what the controller shows and the
masters to fetch. The booth never renders player data itself.
"""

import hashlib
import json
import time

import xxhash

from ic.models import ICProgram

# Bump when the player data's shape changes, so a booth whose AnnotationPlayer.js
# predates it keeps its previous bundle until it's redeployed.
BUNDLE_FORMAT = 1


def manifest_hash(bundle):
    canonical = json.dumps(
        bundle, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def file_xxh64(
    path,
    chunk_size=8 * 1024 * 1024,
    max_bytes_per_second=None,
    on_bytes=None,
    digest=None,
):
    """xxh64 of a file. max_bytes_per_second caps the read rate (the booth hashes from
    the disk the projector is playing from); on_bytes is called after each chunk.
    Pass `digest` to continue an xxh64 already fed with earlier bytes."""
    digest = xxhash.xxh64() if digest is None else digest
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


def _item_entry(item, master):
    return {
        "id": item.pk,
        "title": item.content.title,
        "mode": item.mode,
        "auto_advance": item.auto_advance,
        "subtitles": item.subtitles,
        # A master's checksum is its identity on the booth.
        "master": master and master.checksum,
    }


def _program_entry(program, items):
    return {
        "id": program.pk,
        "name": program.name,
        "start_date": program.start_date.isoformat(),
        "end_date": program.end_date.isoformat(),
        "items": items,
    }


def program_entry(program):
    """A program as the controller shows it: each item, and the master it plays."""
    return _program_entry(
        program,
        [_item_entry(i, i.projection_file()) for i in program.ordered_items()],
    )


def build_bundle(on=None):
    """(bundle, its hash) for the current syncable programs."""
    entries, players = [], {}
    # Each master, with the first and last day any syncable program shows it.
    spans = {}
    for program in ICProgram.syncable(on).order_by("pk"):
        items = []
        for item in program.ordered_items():
            master = item.projection_file()
            items.append(_item_entry(item, master))
            players[str(item.pk)] = player_data(item)
            if master is None:
                continue
            _, first, last = spans.get(
                master.checksum, (master, program.start_date, program.end_date)
            )
            spans[master.checksum] = (
                master,
                min(first, program.start_date),
                max(last, program.end_date),
            )
        entries.append(_program_entry(program, items))
    bundle = {
        "format": BUNDLE_FORMAT,
        "programs": entries,
        "player_data": players,
        "files": [
            {
                "xxh64": master.checksum,
                "name": master.file.name,
                "size": master.size_bytes,
                "first_program_start": first.isoformat(),
                "last_program_end": last.isoformat(),
                "available_from_parent": master.master_released_at is None
                and master.bytes_on_this_server,
            }
            for master, first, last in (spans[key] for key in sorted(spans))
        ],
    }
    return bundle, manifest_hash(bundle)
