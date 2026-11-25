from django.db import transaction

from ..models import BLUR_TIME_PRECISION
from ..models import BlankAnnotation
from ..models import BlurAnchorPoint
from ..models import BlurAnnotation
from ..models import Clip
from ..models import CommentAnnotation
from ..models import MuteAnnotation
from ..models import PauseAnnotation
from ..models import SkipAnnotation
from ..models import Track
from .models import LegacySourceMap

ANNOTATION_MODELS = {
    "skip": SkipAnnotation,
    "mute": MuteAnnotation,
    "pause": PauseAnnotation,
    "comment": CommentAnnotation,
    "blank": BlankAnnotation,
    "censor": BlurAnnotation,
}


def annotation_model_for_type(legacy_type):
    return ANNOTATION_MODELS.get((legacy_type or "").strip().lower())


def blur_anchor_points_from_legacy(position_map, start_time, end_time):
    """A legacy censor's keyframes, in this app's coordinate space and obeying its invariants.

    Legacy stored `{key: [time, centerX, centerY, width, height]}` with the geometry anchored at the
    box's **center**; BlurAnchorPoint stores the top-left corner, one row per time. Importing
    the numbers verbatim would put every box `width/2` right and `height/2` down from where its author
    placed it, leaving the bottom-right of whatever it covered exposed.

    The keys were arbitrary ids, so they carry no ordering and nothing stopped two keyframes sharing a
    moment. That was harmless in an unordered JSON blob and is not here: the unique
    (blur_annotation, time) index added in migration 0004 makes it an IntegrityError, which would
    abort the whole import.

    Returns creation kwargs sorted by time. Corners that convert to negatives are left as they are -
    BlurAnchorPoint.save clamps them onto the frame, which keeps a single definition of what a
    valid box is.
    """
    by_quantized_time = {}
    for values in (position_map or {}).values():
        if not isinstance(values, list) or len(values) < 5:
            continue
        try:
            time, center_x, center_y, width, height = (
                float(value or 0) for value in values[:5]
            )
        except (TypeError, ValueError):
            # Legacy annotations are a free-form blob written by another application, so a non-numeric
            # entry is bad data to skip rather than a reason to fail the import.
            continue
        if time > end_time:
            # Unreachable, and its locator dot would sit past the end of the item's bar.
            continue
        # Quantized here as well as in save(), because the rounded time is what a collision happens
        # on. Among exact ties the last one encountered wins, arbitrarily: legacy keys are ids, so
        # there is no ordering to appeal to.
        by_quantized_time[round(time, BLUR_TIME_PRECISION)] = {
            "time": time,
            "x": center_x - width / 2,
            "y": center_y - height / 2,
            "width": width,
            "height": height,
        }

    blur_anchor_points = [by_quantized_time[time] for time in sorted(by_quantized_time)]
    # Of the keyframes before the censor starts, only the last was still showing when it began;
    # ensure_first_blur_anchor_point pins that one to start_time. Migration 0004 applies the same rule, so a
    # re-import cannot reintroduce data that migration was written to clean up.
    started_early = sum(
        1
        for blur_anchor_point in blur_anchor_points
        if blur_anchor_point["time"] < start_time
    )
    return blur_anchor_points[max(0, started_early - 1) :]


def _record(source_type, source_id, target):
    LegacySourceMap.objects.create(
        source_type=source_type,
        source_id=source_id,
        target_model=type(target).__name__,
        target_id=target.pk,
    )


@transaction.atomic
def import_annotations(annotation_set, content, legacy_annotations, legacy_clips):
    """Fill an empty annotation set with a legacy content's annotations and clips."""
    layer_numbers = [
        int(legacy_event.get("layer", 0) or 0)
        for legacy_event in legacy_annotations
        if annotation_model_for_type(legacy_event.get("type"))
    ]
    # Legacy layers can skip numbers, but the track menus expect positions 0…n-1.
    stack_positions = {
        layer_number: index
        for index, layer_number in enumerate(sorted(set(layer_numbers)))
    }
    tracks_by_layer = {}
    for index, legacy_event in enumerate(legacy_annotations):
        model_class = annotation_model_for_type(legacy_event.get("type"))
        if not model_class:
            continue
        layer_number = int(legacy_event.get("layer", 0) or 0)
        track = tracks_by_layer.get(layer_number)
        if track is None:
            track = Track.objects.create(
                annotation_set=annotation_set,
                name=f"Imported Layer {layer_number}",
                stack_position=stack_positions[layer_number],
            )
            tracks_by_layer[layer_number] = track
        start_time = float(legacy_event.get("start", 0) or 0)
        end_time = float(legacy_event.get("end", start_time) or start_time)
        common_kwargs = {
            "track": track,
            "name": legacy_event.get("title")
            or legacy_event.get("label")
            or legacy_event.get("type", ""),
            "start_time": start_time,
            "end_time": end_time,
            "description": legacy_event.get("description")
            or legacy_event.get("comment")
            or legacy_event.get("type", ""),
            "active": True,
        }

        if model_class is PauseAnnotation:
            # Pause is a point marker: its end time equals its start time.
            annotation = model_class.objects.create(
                message=legacy_event.get("message", ""),
                **{**common_kwargs, "end_time": start_time},
            )
        elif model_class is CommentAnnotation:
            position = legacy_event.get("position") or {}
            x = float(position.get("x", 0) or 0)
            annotation = model_class.objects.create(
                text=legacy_event.get("comment") or legacy_event.get("text") or "",
                x=x,
                y=float(position.get("y", 0) or 0),
                # Legacy comments had no width; their text ran to the frame's right edge.
                width=100.0 - x,
                **common_kwargs,
            )
        elif model_class is BlurAnnotation:
            annotation = model_class.objects.create(**common_kwargs)
            for blur_anchor_point_kwargs in blur_anchor_points_from_legacy(
                legacy_event.get("position"), start_time, end_time
            ):
                BlurAnchorPoint.objects.create(
                    blur_annotation=annotation, **blur_anchor_point_kwargs
                )
            # Legacy censors could arrive with `position: {}` or with an earliest keyframe after
            # their start, and either leaves a blur no user can repair: with no anchor points there is
            # no geometry to render and no create-a-region gesture, and a first anchor point after
            # start_time is skipped by get_blur_anchor_point_dots and readonly in the panel.
            annotation.ensure_first_blur_anchor_point()
        else:
            annotation = model_class.objects.create(**common_kwargs)

        _record("annotation", f"{content.pk}:{index}", annotation)

    if legacy_clips:
        clip_track = Track.objects.create(
            annotation_set=annotation_set,
            name="Imported Clips",
            stack_position=len(stack_positions),
        )
        for clip_index, legacy_clip in enumerate(legacy_clips):
            start_time = float(legacy_clip.get("start", 0) or 0)
            clip = Clip.objects.create(
                track=clip_track,
                name=legacy_clip.get("title")
                or legacy_clip.get("label")
                or f"{content.title} Clip {clip_index + 1}",
                start_time=start_time,
                end_time=float(legacy_clip.get("end", start_time) or start_time),
                description=legacy_clip.get("description", ""),
                active=True,
            )
            _record("clip", f"{content.pk}:{clip_index}", clip)

    content.annotation_set = annotation_set
    content.save(update_fields=["annotation_set", "updated_at"])
