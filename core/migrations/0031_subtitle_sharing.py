import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


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
    ]
