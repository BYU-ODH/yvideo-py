from collections import defaultdict

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


def attach_existing_tracks(apps, schema_editor):
    """Keep every content showing the tracks it showed before tracks were attached explicitly.

    Until now a content showed every subtitle track on its resource.
    """
    Content = apps.get_model("core", "Content")
    Subtitle = apps.get_model("core", "Subtitle")
    ContentSubtitle = Content.subtitle_tracks.through

    track_ids_by_resource = defaultdict(list)
    for track_id, resource_id in Subtitle.objects.values_list("pk", "resource_id"):
        track_ids_by_resource[resource_id].append(track_id)

    rows = [
        ContentSubtitle(content_id=content_id, subtitle_id=track_id)
        for content_id, resource_id in Content.objects.values_list("pk", "resource_id")
        for track_id in track_ids_by_resource[resource_id]
    ]
    ContentSubtitle.objects.bulk_create(rows, batch_size=1000)


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0030_model_consistency"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="content",
            name="subtitle_tracks",
            field=models.ManyToManyField(
                blank=True, related_name="contents", to="core.subtitle"
            ),
        ),
        migrations.AlterField(
            model_name="subtitle",
            name="owner",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="owned_subtitles",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.RunPython(attach_existing_tracks, migrations.RunPython.noop),
    ]
