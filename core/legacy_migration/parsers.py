from dataclasses import dataclass
import datetime
import json
import logging
import re

from django.utils import timezone

from ..models import Resource
from ..utils import VTTCue
from ..utils import build_vtt_file_string_from_cues
from ..utils import seconds2hms
from .models import LegacyMigrationKind


@dataclass
class LegacyFileInfo:
    absolute_path: str = ""
    realpath: str = ""
    size_bytes: int | None = None
    device: int | None = None
    inode: int | None = None
    mtime_ns: int | None = None
    mtime_at: datetime.datetime | None = None
    atime_at: datetime.datetime | None = None
    extension: str = ""
    inspection_error: str = ""


logger = logging.getLogger(__name__)

LEGACY_UUID_RE = re.compile(
    r"(?P<kind>collections|resources)?/?(?P<identifier>[0-9a-fA-F-]{36})"
)
LEGACY_PUBLIC_URL_RE = re.compile(
    r"/(?P<kind>collections|resources)/(?P<identifier>[0-9a-fA-F-]{36})"
)
LEGACY_URL_ONLY_RESOURCE_ID = "00000000-0000-0000-0000-000000000000"


def normalize_name(name):
    normalized = re.sub(r"[^a-z0-9]+", " ", (name or "").lower()).strip()
    return re.sub(r"\s+", " ", normalized)


def parse_legacy_reference(reference, requested_kind=None):
    raw_reference = (reference or "").strip()
    if not raw_reference:
        raise ValueError("A legacy URL or UUID is required.")

    url_match = LEGACY_PUBLIC_URL_RE.search(raw_reference)
    if url_match:
        discovered_kind = (
            LegacyMigrationKind.COLLECTION
            if url_match.group("kind") == "collections"
            else LegacyMigrationKind.RESOURCE
        )
        identifier = url_match.group("identifier")
    else:
        uuid_match = LEGACY_UUID_RE.search(raw_reference)
        if not uuid_match:
            raise ValueError("Could not find a legacy collection/resource UUID.")
        discovered_kind = requested_kind
        identifier = uuid_match.group("identifier")

    if requested_kind and discovered_kind and requested_kind != discovered_kind:
        raise ValueError(
            f"The provided reference points to a {discovered_kind}, not a {requested_kind}."
        )
    return discovered_kind or requested_kind, identifier


def timestamp_to_datetime(raw_timestamp):
    if raw_timestamp in (None, ""):
        return None
    if isinstance(raw_timestamp, datetime.datetime):
        return (
            raw_timestamp
            if timezone.is_aware(raw_timestamp)
            else timezone.make_aware(raw_timestamp)
        )
    return datetime.datetime.fromtimestamp(float(raw_timestamp), tz=datetime.UTC)


def json_loads_loose(value, default=None):
    if value in (None, ""):
        return [] if default is None else default
    if isinstance(value, (list, dict)):
        return value
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return [] if default is None else default


def parse_legacy_annotations(raw_annotations):
    if raw_annotations in (None, ""):
        return []
    parsed = json_loads_loose(raw_annotations, default=None)
    if parsed is not None:
        return parsed if isinstance(parsed, list) else []

    annotations = []
    for part in [item.strip() for item in raw_annotations.split("; ") if item.strip()]:
        try:
            annotations.append(json.loads(part))
        except json.JSONDecodeError:
            logger.warning("Skipping invalid legacy annotation payload: %s", part)
    return annotations


def parse_legacy_clips(raw_clips):
    clips = json_loads_loose(raw_clips, default=[])
    return clips if isinstance(clips, list) else []


def build_subtitle_vtt(raw_content):
    cues = []
    for cue in json_loads_loose(raw_content, default=[]):
        cues.append(
            VTTCue(
                type="CUE",
                payload=cue.get("text", ""),
                start_time=seconds2hms(float(cue.get("start", 0) or 0)),
                end_time=seconds2hms(float(cue.get("end", 0) or 0)),
            )
        )
    return build_vtt_file_string_from_cues(cues)


def map_legacy_media_type(legacy_value):
    value = (legacy_value or "").strip().lower()
    if value in {"vid", "video"}:
        return Resource.MediaType.VIDEO
    if value in {"aud", "audio"}:
        return Resource.MediaType.AUDIO
    if value in {"txt", "text"}:
        return Resource.MediaType.TEXT
    if value in {"www", "web"}:
        return Resource.MediaType.WEB
    return Resource.MediaType.VIDEO


def make_json_safe(value):
    if isinstance(value, dict):
        return {key: make_json_safe(subvalue) for key, subvalue in value.items()}
    if isinstance(value, list):
        return [make_json_safe(item) for item in value]
    if isinstance(value, tuple):
        return [make_json_safe(item) for item in value]
    if isinstance(value, datetime.datetime):
        return value.isoformat()
    return value


def build_user_fingerprint(user_dict):
    parts = [
        user_dict.get("legacy_user_id", ""),
        user_dict.get("legacy_username", ""),
        user_dict.get("legacy_byu_id", ""),
        user_dict.get("legacy_email", ""),
    ]
    return "|".join(parts)
