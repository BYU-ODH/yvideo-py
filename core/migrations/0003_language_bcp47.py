import django.core.validators
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0002_content_clips_only"),
    ]

    operations = [
        migrations.RenameField(
            model_name="language",
            old_name="lang_tag",
            new_name="bcp47",
        ),
        migrations.AlterField(
            model_name="language",
            name="bcp47",
            field=models.CharField(
                help_text="BCP 47 primary language subtag: the 2-letter ISO 639-1 "
                "code where one exists (e.g. en, es), otherwise the 3-letter "
                "ISO 639-3 code (e.g. ase, cak). See "
                "https://www.rfc-editor.org/rfc/rfc5646",
                max_length=3,
                unique=True,
                validators=[
                    django.core.validators.RegexValidator(
                        code="invalid_bcp47",
                        message="Must be a 2- or 3-letter lowercase BCP 47 language subtag (e.g., en, ceb).",
                        regex="^[a-z]{2,3}$",
                    )
                ],
            ),
        ),
    ]
