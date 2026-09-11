import logging

from django.db import migrations

logger = logging.getLogger(__name__)


def delete_playlistless_content(apps, schema_editor):
    """Content with no playlist is unreachable and now has no permission story.

    can_be_viewed_by and can_be_edited_by both derive from the playlist, and the
    endpoint that created these orphans (remove_content_from_playlist) is gone.
    Annotation sets are untouched: AnnotationSet is a separate table and
    Content.annotation_set is the nullable side of the FK, so this cannot reach them.
    """
    Content = apps.get_model("core", "Content")
    orphaned = Content.objects.filter(playlist__isnull=True)
    count = orphaned.count()
    if count:
        logger.warning("Deleting %s Content rows that have no playlist", count)
        orphaned.delete()


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0006_yearterm_alter_annotationset_unique_together_and_more"),
    ]

    operations = [
        migrations.RunPython(
            delete_playlistless_content, migrations.RunPython.noop, elidable=False
        ),
    ]
