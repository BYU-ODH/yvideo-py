from django.db import migrations, models


def delete_tokenless_auth_tokens(apps, schema_editor):
    # Api re-issues a token whenever the stored one is missing or stale.
    apps.get_model("core", "AuthToken").objects.filter(token__isnull=True).delete()


def fill_user_course_yearterms(apps, schema_editor):
    UserCourses = apps.get_model("core", "UserCourses")
    for user_course in UserCourses.objects.select_related("course"):
        user_course.yearterm = user_course.course.yearterm
        user_course.save(update_fields=["yearterm"])


def blank_null_content_urls(apps, schema_editor):
    apps.get_model("core", "Content").objects.filter(url__isnull=True).update(url="")


def fill_content_resource_from_file(apps, schema_editor):
    # Code reads Content.resource directly; fill it for rows written without save().
    Content = apps.get_model("core", "Content")
    for content in Content.objects.filter(resource_file__isnull=False).select_related(
        "resource_file"
    ):
        if content.resource_id != content.resource_file.resource_id:
            content.resource_id = content.resource_file.resource_id
            content.save(update_fields=["resource"])


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0029_remove_legacy_migration_requests"),
    ]

    operations = [
        migrations.DeleteModel(name="Email"),
        migrations.RunPython(delete_tokenless_auth_tokens, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="authtoken",
            name="token",
            field=models.CharField(max_length=150),
        ),
        migrations.RemoveConstraint(
            model_name="usercourses",
            name="unique_user_course_yearterm",
        ),
        # A default lets a reversal re-add the column before refilling it.
        migrations.AlterField(
            model_name="usercourses",
            name="yearterm",
            field=models.CharField(default="", max_length=5),
        ),
        migrations.RunPython(migrations.RunPython.noop, fill_user_course_yearterms),
        migrations.RemoveField(
            model_name="usercourses",
            name="yearterm",
        ),
        migrations.AddConstraint(
            model_name="usercourses",
            constraint=models.UniqueConstraint(
                fields=("user", "course"), name="unique_user_course"
            ),
        ),
        migrations.RemoveConstraint(
            model_name="content",
            name="content_has_resource_file_or_url",
        ),
        migrations.RunPython(blank_null_content_urls, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="content",
            name="url",
            field=models.URLField(blank=True, max_length=500),
        ),
        migrations.AddConstraint(
            model_name="content",
            constraint=models.CheckConstraint(
                condition=models.Q(resource_file__isnull=False) | ~models.Q(url=""),
                name="content_has_resource_file_or_url",
                violation_error_message="Content needs a resource file or a URL.",
            ),
        ),
        migrations.RunPython(
            fill_content_resource_from_file, migrations.RunPython.noop
        ),
        migrations.AlterField(
            model_name="track",
            name="name",
            field=models.CharField(max_length=50),
        ),
    ]
