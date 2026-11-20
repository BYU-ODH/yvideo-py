from django.db import migrations, models


def delete_tokenless_auth_tokens(apps, schema_editor):
    # Api re-issues a token whenever the stored one is missing or stale.
    apps.get_model("core", "AuthToken").objects.filter(token__isnull=True).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0029_remove_legacy_migration_requests"),
    ]

    operations = [
        migrations.AlterModelOptions(
            name="email",
            options={"ordering": ["-sent_at"]},
        ),
        migrations.RemoveField(
            model_name="email",
            name="created_at",
        ),
        migrations.RunPython(delete_tokenless_auth_tokens, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="authtoken",
            name="token",
            field=models.CharField(max_length=150),
        ),
    ]
