from django.db import migrations
from django.db import models

COLLABORATOR = 0
LEGACY_TA = 1


def tas_become_collaborators(apps, schema_editor):
    """PlaylistRole merged TA into COLLABORATOR; the two never differed in access."""
    PlaylistUserAccess = apps.get_model("core", "PlaylistUserAccess")
    PlaylistUserAccess.objects.filter(playlist_role=LEGACY_TA).update(
        playlist_role=COLLABORATOR
    )


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0013_remove_skipannotation_message"),
    ]

    operations = [
        migrations.RunPython(
            tas_become_collaborators, migrations.RunPython.noop, elidable=True
        ),
        migrations.AlterField(
            model_name="playlistuseraccess",
            name="playlist_role",
            field=models.IntegerField(
                choices=[(0, "Collaborator"), (2, "Student")], default=2
            ),
        ),
    ]
