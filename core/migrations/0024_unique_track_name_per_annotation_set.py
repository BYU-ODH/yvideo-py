import itertools
import re

from django.db import migrations, models


def rename_duplicate_and_blank_tracks(apps, schema_editor):
    """Give repeated or blank track names a fresh "Track <n>" so the constraint can apply.

    The top track (lowest stack position) keeps a repeated name. Numbering mirrors
    AnnotationSet.next_default_track_name, which historical models cannot call.
    """
    Track = apps.get_model("core", "Track")
    tracks = Track.objects.order_by("annotation_set_id", "stack_position", "pk")
    for _, set_tracks in itertools.groupby(tracks, key=lambda t: t.annotation_set_id):
        set_tracks = list(set_tracks)
        numbers = [
            int(match[1])
            for track in set_tracks
            if (match := re.fullmatch(r"Track (\d+)", track.name))
        ]
        next_number = max(numbers, default=0) + 1
        seen = set()
        for track in set_tracks:
            if track.name.strip() and track.name not in seen:
                seen.add(track.name)
                continue
            track.name = f"Track {next_number}"
            next_number += 1
            track.save(update_fields=["name"])


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0023_clip_description_help_text"),
    ]

    operations = [
        migrations.RunPython(
            rename_duplicate_and_blank_tracks, migrations.RunPython.noop
        ),
        migrations.AddConstraint(
            model_name="track",
            constraint=models.UniqueConstraint(
                fields=("annotation_set", "name"),
                name="unique_track_name_per_annotation_set",
            ),
        ),
    ]
