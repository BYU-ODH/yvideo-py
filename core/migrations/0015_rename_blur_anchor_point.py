from django.db import migrations
from django.db import models


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0014_merge_ta_into_collaborator"),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="blurannotationposition",
            name="unique_blur_annotation_position_time",
        ),
        migrations.RenameModel(
            old_name="BlurAnnotationPosition",
            new_name="BlurAnchorPoint",
        ),
        migrations.AlterField(
            model_name="bluranchorpoint",
            name="blur_annotation",
            field=models.ForeignKey(
                on_delete=models.deletion.CASCADE,
                related_name="blur_anchor_points",
                to="core.blurannotation",
            ),
        ),
        migrations.AddConstraint(
            model_name="bluranchorpoint",
            constraint=models.UniqueConstraint(
                fields=("blur_annotation", "time"),
                name="unique_blur_anchor_point_time",
            ),
        ),
    ]
