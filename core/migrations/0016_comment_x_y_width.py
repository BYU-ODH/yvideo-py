from django.db import migrations
from django.db import models
from django.db.models import Case
from django.db.models import F
from django.db.models import Value
from django.db.models import When
from django.db.models.functions import Greatest
from django.db.models.functions import Least
from django.db.models.functions import Round


def corners_to_width(apps, schema_editor):
    CommentAnnotation = apps.get_model("core", "CommentAnnotation")
    # Many stored corners are inverted. The player rejected their negative CSS width and let the
    # text run to the frame's right edge, so that is the width they keep.
    CommentAnnotation.objects.update(
        width=Case(
            When(
                bottom_right_x__lte=F("top_left_x"),
                then=Value(100.0) - F("top_left_x"),
            ),
            default=F("bottom_right_x") - F("top_left_x"),
        )
    )
    # The same clamps and rounding CommentAnnotation.save() applies.
    CommentAnnotation.objects.update(
        width=Round(Least(Value(100.0), Greatest(Value(3.0), F("width"))), 2)
    )
    CommentAnnotation.objects.update(
        top_left_x=Round(
            Least(Value(100.0) - F("width"), Greatest(Value(0.0), F("top_left_x"))), 2
        ),
        top_left_y=Round(Least(Value(97.0), Greatest(Value(0.0), F("top_left_y"))), 2),
    )


def width_to_corners(apps, schema_editor):
    CommentAnnotation = apps.get_model("core", "CommentAnnotation")
    CommentAnnotation.objects.update(
        bottom_right_x=F("top_left_x") + F("width"),
        bottom_right_y=F("top_left_y") + Value(10.0),
    )


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0015_rename_blur_anchor_point"),
    ]

    operations = [
        migrations.AddField(
            model_name="commentannotation",
            name="width",
            field=models.FloatField(default=100.0),
        ),
        migrations.RunPython(corners_to_width, width_to_corners),
        migrations.RemoveField(
            model_name="commentannotation",
            name="bottom_right_x",
        ),
        migrations.RemoveField(
            model_name="commentannotation",
            name="bottom_right_y",
        ),
        migrations.RenameField(
            model_name="commentannotation",
            old_name="top_left_x",
            new_name="x",
        ),
        migrations.RenameField(
            model_name="commentannotation",
            old_name="top_left_y",
            new_name="y",
        ),
    ]
