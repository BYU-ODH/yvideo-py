"""Bring existing blur positions in line with the invariants, then enforce them.

Three things change here, in this order, because the constraint cannot be added until the
data satisfies it:

  1. Normalize every blur's positions (see _normalize).
  2. Order positions by time by default.
  3. Require (blur_annotation, time) to be unique.

Geometry is normalized here as well as timing. BlurAnnotationPosition.save() is the only place the
geometry rules are enforced and there is no database constraint behind it, so rows written before
this migration keep whatever they had - boxes hanging off the right edge, boxes too small to grab -
and nothing on the read path corrects them.

The interesting case is the "sentinel" position. `create_annotation` used to seed every new blur
with a `time=0, x=50, y=50, width=4, height=3` row regardless of where the blur actually started,
and several code paths had special cases to keep that row alive and hidden from the user. The
invariant that replaces all of it is simply: the earliest position sits at the blur's start_time. So
sentinels get dropped where a real position exists, and promoted to start_time where they are all a
blur has.
"""

from django.db import migrations
from django.db import models

# Inlined rather than imported from core.models: migrations must keep working when the constants they
# were written against later change or move.
TIME_PRECISION = 2
GEOMETRY_PRECISION = 2
MIN_WIDTH = 3.0
MIN_HEIGHT = 4.0
DEFAULT_GEOMETRY = {"x": 40.0, "y": 42.5, "width": 20.0, "height": 15.0}
# The exact geometry create_annotation used to seed. Together with time=0 this is a reliable
# fingerprint for a row the user never positioned themselves.
SENTINEL_GEOMETRY = {"x": 50.0, "y": 50.0, "width": 4.0, "height": 3.0}

GEOMETRY_FIELDS = ("x", "y", "width", "height")


def _is_sentinel(position):
    return position.time == 0 and all(
        getattr(position, field) == value for field, value in SENTINEL_GEOMETRY.items()
    )


def _clamp_geometry(position):
    """Quantize and clamp one row's box, mirroring BlurAnnotationPosition.save().

    Deliberately a second implementation rather than a call: historical models carry no custom
    methods, so save() is unavailable here, and a migration that imported it would change
    meaning the next time those rules did. Held to the same case table as the model - see
    core/tests/test_blur_positions.py and core/tests/test_blur_position_migration.py.

    Order matches the model's: size settles first, then the origin is pulled in far enough to
    keep the whole box on the frame.
    """
    width = round(min(100.0, max(MIN_WIDTH, float(position.width))), GEOMETRY_PRECISION)
    height = round(
        min(100.0, max(MIN_HEIGHT, float(position.height))), GEOMETRY_PRECISION
    )
    clamped = {
        "width": width,
        "height": height,
        "x": round(min(100.0 - width, max(0.0, float(position.x))), GEOMETRY_PRECISION),
        "y": round(
            min(100.0 - height, max(0.0, float(position.y))), GEOMETRY_PRECISION
        ),
    }
    if all(getattr(position, field) == clamped[field] for field in GEOMETRY_FIELDS):
        return
    for field, value in clamped.items():
        setattr(position, field, value)
    position.save(update_fields=list(GEOMETRY_FIELDS))


def _normalize(apps, schema_editor):
    """Make every blur satisfy the invariants. Safe to run more than once."""
    BlurAnnotation = apps.get_model("core", "BlurAnnotation")
    BlurAnnotationPosition = apps.get_model("core", "BlurAnnotationPosition")

    for blur in BlurAnnotation.objects.all().iterator():
        positions = list(blur.positions.all().order_by("time", "pk"))

        # Rounding is what *creates* most collisions (18.001 and 18.004 both become 18.0), so the
        # losers have to be deleted before the survivor is retimed onto the shared value - otherwise
        # the write collides with a row that is about to be removed.
        groups = {}
        for position in positions:
            groups.setdefault(round(float(position.time), TIME_PRECISION), []).append(
                position
            )

        positions = []
        for rounded in sorted(groups):
            group = sorted(groups[rounded], key=lambda p: p.pk)
            # Highest pk wins: the most recent edit, the one the user last saw take effect.
            survivor = group[-1]
            for loser in group[:-1]:
                loser.delete()
            if survivor.time != rounded:
                survivor.time = rounded
                survivor.save(update_fields=["time"])
            positions.append(survivor)

        # Drop anything past the end of the window; it can never be reached.
        for position in [p for p in positions if p.time > blur.end_time]:
            position.delete()
        positions = [p for p in positions if p.time <= blur.end_time]

        # Of the positions before the window, only the last one carries geometry worth
        # keeping: it is what was on screen when the blur began.
        before_start = [p for p in positions if p.time < blur.start_time]
        if before_start:
            keep = before_start[-1]
            # ...unless it is an untouched sentinel and there is real geometry to fall back
            # on, in which case it is noise rather than data.
            if _is_sentinel(keep) and len(positions) > len(before_start):
                to_delete, keep = before_start, None
            else:
                to_delete = before_start[:-1]
            for position in to_delete:
                position.delete()
            positions = [p for p in positions if p.time >= blur.start_time]
            if keep is not None:
                positions.insert(0, keep)

        if not positions:
            BlurAnnotationPosition.objects.create(
                blur_annotation=blur,
                time=round(float(blur.start_time), TIME_PRECISION),
                **DEFAULT_GEOMETRY,
            )
            continue

        # A sentinel that is all a blur has is the seed row, not a box the user placed, and 4x3 of the
        # frame is the all-but-invisible smudge DEFAULT_GEOMETRY exists to replace - so repinning it
        # and stopping there would leave the blur as unusable as before.
        if len(positions) == 1 and _is_sentinel(positions[0]):
            for field, value in DEFAULT_GEOMETRY.items():
                setattr(positions[0], field, value)
            positions[0].save(update_fields=list(DEFAULT_GEOMETRY))

        # Pin the earliest position to start_time. Guarded against colliding with a position that
        # already sits exactly there.
        earliest = positions[0]
        target = round(float(blur.start_time), TIME_PRECISION)
        if earliest.time != target:
            if any(p.pk != earliest.pk and p.time == target for p in positions):
                earliest.delete()
                positions = positions[1:]
            else:
                earliest.time = target
                earliest.save(update_fields=["time"])

        # Last, because the steps above decide *which* rows exist and clamping one that is about to be
        # deleted is wasted work.
        for position in positions:
            _clamp_geometry(position)


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0003_language_bcp47"),
    ]

    operations = [
        # Reversing cannot restore sentinels, and nothing depends on them once the invariants hold.
        migrations.RunPython(_normalize, migrations.RunPython.noop),
        migrations.AlterModelOptions(
            name="blurannotationposition",
            options={"ordering": ["time"]},
        ),
        migrations.AddConstraint(
            model_name="blurannotationposition",
            constraint=models.UniqueConstraint(
                fields=("blur_annotation", "time"),
                name="unique_blur_annotation_position_time",
            ),
        ),
    ]
