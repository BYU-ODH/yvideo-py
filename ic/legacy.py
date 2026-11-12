"""Import the Electron IC Player's annotation JSON (IC_player/JSONFormat.md) as Y-video rows.

convert_legacy_annotations mirrors AnnotationPlayer.parseICLegacyAnnotations and
icPositionsToBlurAnchorPoints in core/static/js/AnnotationPlayer.js; a parity test holds
the two together, so a film imported here plays exactly as the old JSON did.
"""

from dataclasses import dataclass
from dataclasses import field
import json
from pathlib import Path
import shutil

from django.db import transaction

from core.models import AnnotationSet
from core.models import BlankAnnotation
from core.models import BlurAnchorPoint
from core.models import BlurAnnotation
from core.models import MuteAnnotation
from core.models import SkipAnnotation
from core.models import Track

DEFAULT_BLUR_SIZE = 15


def positions_to_anchor_points(position_map):
    """{"time": [x, y, (width, height)]} → anchor points; missing sizes carry forward."""
    points = []
    for time_key in sorted((position_map or {}).keys(), key=float):
        values = list(position_map[time_key])
        x, y = values[0], values[1]
        width = values[2] if len(values) > 2 else None
        height = values[3] if len(values) > 3 else None
        previous = points[-1] if points else None
        points.append(
            {
                "time": float(time_key),
                "x": x,
                "y": y,
                "width": width
                if width is not None
                else (previous["width"] if previous else DEFAULT_BLUR_SIZE),
                "height": height
                if height is not None
                else (previous["height"] if previous else DEFAULT_BLUR_SIZE),
            }
        )
    return points


def convert_legacy_annotations(legacy):
    """The legacy list as the player-shaped dicts parseICLegacyAnnotations produces."""
    converted = []
    for entry in legacy:
        options = entry["options"]
        legacy_type = options["type"]
        # Legacy "censor" is a positional blur box; legacy "blur" is whole-screen blur.
        kind = {"censor": "blur", "blur": "screenBlur"}.get(legacy_type, legacy_type)
        annotation = {
            "label": options.get("label") or "",
            "start": float(options["start"]),
            "end": float(options["end"]),
            "type": kind,
        }
        if kind == "blur":
            annotation["positions"] = positions_to_anchor_points(
                (options.get("details") or {}).get("position")
            )
        converted.append(annotation)
    return converted


@dataclass
class ImportResult:
    content: object
    annotation_set: object
    counts: dict = field(default_factory=dict)
    skipped: list = field(default_factory=list)
    upload: object = None


def read_icf(icf_path):
    icf_path = Path(icf_path)
    data = json.loads(icf_path.read_text())
    base = icf_path.parent
    annotation = base / data["annotation"] if data.get("annotation") else None
    video = base / ".ic" / data["video"] if data.get("video") else None
    return annotation, video


def import_film(icf_path, content, *, owner=None, with_video=False, dry_run=False):
    """Create an annotation set on `content` from an .icf's JSON (and optionally its video)."""
    annotation_path, video_path = read_icf(icf_path)
    legacy = json.loads(annotation_path.read_text()) if annotation_path else []
    converted = convert_legacy_annotations(legacy)
    owner = owner or content.playlist.owner
    counts = {}
    skipped = []
    with transaction.atomic():
        name = f"IC import: {Path(icf_path).stem}"
        existing = AnnotationSet.objects.filter(
            name__startswith=name, resource=content.resource, owner=owner
        ).count()
        annotation_set = AnnotationSet.objects.create(
            name=f"{name} ({existing + 1})" if existing else name,
            resource=content.resource,
            owner=owner,
        )
        track = Track.objects.create(annotation_set=annotation_set, name="IC import")
        for annotation in converted:
            common = {
                "track": track,
                "name": annotation["label"],
                "start_time": annotation["start"],
                "end_time": annotation["end"],
            }
            kind = annotation["type"]
            if kind == "skip":
                SkipAnnotation.objects.create(**common)
            elif kind == "mute":
                MuteAnnotation.objects.create(**common)
            elif kind == "blank":
                BlankAnnotation.objects.create(type="k", **common)
            elif kind == "screenBlur":
                BlankAnnotation.objects.create(type="#", **common)
            elif kind == "blur":
                blur = BlurAnnotation.objects.create(**common)
                for point in annotation["positions"]:
                    BlurAnchorPoint.objects.create(blur_annotation=blur, **point)
            else:
                skipped.append(annotation)
                continue
            counts[kind] = counts.get(kind, 0) + 1
        content.annotation_set = annotation_set
        content.save(update_fields=["annotation_set", "updated_at"])

        upload = None
        if with_video and video_path is not None:
            upload = _queue_master_upload(video_path, content, owner)
        if dry_run:
            transaction.set_rollback(True)
    return ImportResult(content, annotation_set, counts, skipped, upload)


def _queue_master_upload(video_path, content, owner):
    """Hand the legacy video to the normal ingest pipeline as this film's master.

    It is exactly what the legacy annotations were timed against, so the content is
    switched to the SD file made from it once the transcode finishes.
    """
    from .jobs import queue
    from .models import ICUpload
    from .models import MediaJob

    size = Path(video_path).stat().st_size
    upload = ICUpload.objects.create(
        resource=content.resource,
        owner=owner,
        filename=Path(video_path).name,
        expected_size=size,
        received_bytes=size,
        status=ICUpload.Status.PROCESSING,
    )
    upload.part_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(video_path, upload.part_path)
    upload.job = queue(
        MediaJob.Kind.INGEST,
        payload={"upload_id": str(upload.pk), "switch_content_ids": [content.pk]},
    )
    upload.save(update_fields=["job", "updated_at"])
    return upload
