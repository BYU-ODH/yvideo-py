from django.db import migrations
from django.db import models

LEGACY_AUDITOR = 3
STUDENT = 2


def auditors_become_students(apps, schema_editor):
    """PlaylistRole dropped AUDITOR (#361); it never differed from STUDENT.

    Nothing is deployed, so this exists for development databases seeded from a legacy
    import: an unconverted row renders a blank <select> in Manage People rather than
    failing anywhere visible.
    """
    PlaylistUserAccess = apps.get_model("core", "PlaylistUserAccess")
    PlaylistUserAccess.objects.filter(playlist_role=LEGACY_AUDITOR).update(
        playlist_role=STUDENT
    )


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0010_remove_content_words_remove_subtitle_words_and_more"),
    ]

    operations = [
        migrations.RunPython(
            auditors_become_students, migrations.RunPython.noop, elidable=True
        ),
        migrations.AlterField(
            model_name="playlistuseraccess",
            name="playlist_role",
            field=models.IntegerField(
                choices=[(0, "Co-instructor"), (1, "TA"), (2, "Student")], default=2
            ),
        ),
    ]
