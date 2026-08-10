"""Uniqueness for the two identifiers Resource and ResourceFile are deduped on.

`Resource.imdb_id` becoming unique is what lets YouTube-backed Resources be get-or-created
by ``YT<video-id>`` (see core/youtube.py), so this constraint is load-bearing for that
feature rather than incidental tidying.

Split into widen -> backfill -> constrain because adding the constraint in one step fails on
any database that already holds data: before this, three paths created Resources with no
imdb_id at all, leaving duplicate empty strings behind - the legacy import
(core/legacy_migration/service.py), ResourceFactory (dev seed data), and an intake request
resolved with "Resource is not in IMDb" checked, which passed "" and relied on a
generate_internal_imdb_id() call that now lives in Resource.save().

`_backfill` writes out the same id format as Resource.generate_internal_imdb_id. Historical
models carry no custom methods, so the format cannot be called here; the two copies are held
to one answer by core/tests/test_resource_identifier_migration.py.

ResourceFile.barcode needs no backfill: it was already nullable, and NULLs do not collide
under a unique constraint. It will still fail on duplicate *non-null* barcodes, which nothing
previously prevented - `_reject_duplicate_barcodes` reports them up front rather than letting
the schema alteration fail with a message that names no rows.
"""

from django.db import migrations
from django.db import models

import core.models


def _backfill(apps, schema_editor):
    Resource = apps.get_model("core", "Resource")
    # "" and NULL both mean "no id assigned", and only NULL survives the unique constraint,
    # so collapse the two before handing out generated ids.
    Resource.objects.filter(imdb_id="").update(imdb_id=None)
    for pk in Resource.objects.filter(imdb_id=None).values_list("pk", flat=True):
        # Mirrors Resource.generate_internal_imdb_id.
        Resource.objects.filter(pk=pk).update(imdb_id="BYU" + str(pk).zfill(10))


def _restore_blanks(apps, schema_editor):
    # Only so the reverse AlterField (which drops null=True) has no NULLs to choke on.
    # Generated ids are deliberately left in place: nothing records which rows were
    # generated, and a BYU id is valid under the old field too.
    apps.get_model("core", "Resource").objects.filter(imdb_id=None).update(imdb_id="")


def _duplicated(values):
    """The values appearing more than once, sorted.

    Kept separate from the query below, and pure, because the state it guards against cannot
    be created once the constraint exists - including in a test database, which is migrated
    all the way up before any test runs. Testing the rule is therefore the only way to test
    it at all. See core/tests/test_resource_identifier_migration.py.
    """
    seen = set()
    duplicates = set()
    for value in values:
        if value in seen:
            duplicates.add(value)
        seen.add(value)
    return sorted(duplicates)


def _reject_duplicate_barcodes(apps, schema_editor):
    ResourceFile = apps.get_model("core", "ResourceFile")
    duplicates = _duplicated(
        ResourceFile.objects.exclude(barcode=None)
        .exclude(barcode="")
        .values_list("barcode", flat=True)
    )
    if duplicates:
        raise RuntimeError(
            "ResourceFile.barcode cannot be made unique while these barcodes are "
            f"shared by more than one file: {duplicates}. Resolve them (a barcode "
            "identifies one physical item, so at most one file should carry each) and "
            "re-run this migration."
        )


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0004_blur_position_invariants"),
    ]

    operations = [
        migrations.AlterField(
            model_name="resource",
            name="imdb_id",
            field=models.CharField(
                blank=True, null=True, validators=[core.models.validate_imdb_id]
            ),
        ),
        migrations.RunPython(_backfill, _restore_blanks),
        migrations.AlterField(
            model_name="resource",
            name="imdb_id",
            field=models.CharField(
                blank=True,
                null=True,
                unique=True,
                validators=[core.models.validate_imdb_id],
            ),
        ),
        migrations.RunPython(_reject_duplicate_barcodes, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="resourcefile",
            name="barcode",
            field=models.CharField(
                blank=True,
                help_text=(
                    "The EAN or UPC barcode on the resource. If there isn't one, an "
                    "internal 'BYU' prefixed code will be assigned."
                ),
                null=True,
                unique=True,
            ),
        ),
    ]
