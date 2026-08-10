"""Tests for the 0005 data migration that backfills Resource.imdb_id before it becomes unique.

Driven the same way as core/tests/test_blur_position_migration.py: the migration's helpers are
called directly against a real historical registry, rebuilt from the migration graph at 0005's
dependency, rather than through the migration executor. Historical models carry no custom
methods, which is the whole point here - the migration cannot call
Resource.generate_internal_imdb_id and has to write the id format out a second time, so the
case that matters most is the one holding those two copies to one answer.

The rows this migration exists for are the ones no test fixture produces: Resources created
before imdb_id was auto-assigned, all sharing the empty string, which is precisely the state a
one-step AlterField(unique=True) cannot survive.
"""

import importlib

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase
from django.test import TransactionTestCase

from core.factories import ResourceFactory
from core.factories import ResourceFileFactory
from core.models import Resource

MIGRATION = importlib.import_module(
    "core.migrations.0005_resource_identifier_uniqueness"
)

# The state *before* 0005 runs, which is what RunPython is handed. Built once: it reads the
# migration graph off disk and touches no database rows, so it is safe to share.
_HISTORICAL_APPS = None


def _historical_apps():
    global _HISTORICAL_APPS
    if _HISTORICAL_APPS is None:
        loader = MigrationExecutor(connection).loader
        _HISTORICAL_APPS = loader.project_state(
            ("core", "0004_blur_position_invariants")
        ).apps
    return _HISTORICAL_APPS


def _clear_imdb_id(resource, value=""):
    """Blank a Resource's id the way a pre-0005 row was left.

    Goes around save(), which is what assigns an id in the first place.
    """
    Resource.objects.filter(pk=resource.pk).update(imdb_id=value)


def _backfill():
    MIGRATION._backfill(_historical_apps(), connection.schema_editor())


class BackfillImdbIdTests(TestCase):
    def test_rows_with_no_id_all_get_distinct_ids(self):
        # Several at once, because the ids are handed out in a loop: a backfill that derived
        # them from anything but the row's own pk would pass with one row and collide here.
        # NULL rather than "" only because the test database already enforces the constraint
        # (see MigrationRoundTripTests, which builds the real "" case).
        resources = [ResourceFactory() for _ in range(3)]
        for resource in resources:
            _clear_imdb_id(resource, value=None)

        _backfill()

        ids = [Resource.objects.get(pk=r.pk).imdb_id for r in resources]
        self.assertEqual(len(set(ids)), 3)
        for value in ids:
            self.assertRegex(value, r"^BYU\d{10}$")

    def test_an_empty_string_is_treated_as_no_id(self):
        resource = ResourceFactory()
        _clear_imdb_id(resource, value="")

        _backfill()

        self.assertRegex(Resource.objects.get(pk=resource.pk).imdb_id, r"^BYU\d{10}$")

    def test_an_existing_id_is_left_alone(self):
        resource = ResourceFactory()
        _clear_imdb_id(resource, value="tt0111161")

        _backfill()

        self.assertEqual(Resource.objects.get(pk=resource.pk).imdb_id, "tt0111161")

    def test_generated_ids_match_generate_internal_imdb_id(self):
        # The parity case. The migration writes the "BYU" + zero-padded-pk format out by hand
        # because a historical model has no generate_internal_imdb_id to call; if the two ever
        # disagree, asking the live model to generate an id for a row the migration already
        # handled would change it.
        resource = ResourceFactory()
        _clear_imdb_id(resource)

        _backfill()

        migrated = Resource.objects.get(pk=resource.pk)
        from_migration = migrated.imdb_id
        migrated.generate_internal_imdb_id()
        self.assertEqual(migrated.imdb_id, from_migration)

    def test_reverse_turns_nulls_back_into_blanks(self):
        # So the reverse AlterField, which drops null=True, has no NULLs to choke on.
        resource = ResourceFactory()
        _clear_imdb_id(resource, value=None)

        MIGRATION._restore_blanks(_historical_apps(), connection.schema_editor())

        self.assertEqual(Resource.objects.get(pk=resource.pk).imdb_id, "")


class MigrationRoundTripTests(TransactionTestCase):
    """0005 driven through the migration executor, down to 0004 and back up.

    This is the only place the state 0005 exists for can actually be built: unapplying it drops
    the unique constraint, which is what lets two Resources hold the same empty-string id. It
    doubles as the reversibility check - a migration that cannot be unapplied cannot be rolled
    back in production either.
    """

    def _migrate(self, target):
        # A fresh executor each time: the loader caches the applied-migrations graph, which the
        # previous migrate() call just invalidated.
        MigrationExecutor(connection).migrate([("core", target)])

    def setUp(self):
        self.addCleanup(self._migrate, "0005_resource_identifier_uniqueness")

    def test_duplicate_blank_ids_are_backfilled_by_the_real_migration(self):
        self._migrate("0004_blur_position_invariants")

        # The historical model, so save() cannot quietly assign an id the way the live one does.
        # A CharField with no value inserts "", which is exactly what a pre-0005 row holds.
        historical = (
            MigrationExecutor(connection)
            .loader.project_state(("core", "0004_blur_position_invariants"))
            .apps
        )
        HistoricalResource = historical.get_model("core", "Resource")
        pks = [
            HistoricalResource.objects.create(
                name=f"Blank Id Resource {n}", requester_username=f"req{n:05d}"
            ).pk
            for n in range(3)
        ]
        self.assertEqual(
            list(
                HistoricalResource.objects.filter(pk__in=pks).values_list(
                    "imdb_id", flat=True
                )
            ),
            ["", "", ""],
        )

        self._migrate("0005_resource_identifier_uniqueness")

        ids = list(
            Resource.objects.filter(pk__in=pks).values_list("imdb_id", flat=True)
        )
        self.assertEqual(len(set(ids)), 3)
        for value in ids:
            self.assertRegex(value, r"^BYU\d{10}$")


class DuplicateBarcodePreflightTests(TestCase):
    """The barcode half, which guards an AlterField rather than backfilling anything.

    Its failing state - two files sharing a barcode - cannot be created here: the test database
    is migrated to the head, so the unique constraint this check exists to explain is already
    enforced. Hence the rule is tested as a rule, and the query is tested only for the shape it
    has to get right (skipping rows with no barcode, which legitimately repeat).
    """

    def test_values_appearing_more_than_once_are_reported_sorted(self):
        self.assertEqual(
            MIGRATION._duplicated(["b", "a", "b", "c", "a", "b"]), ["a", "b"]
        )

    def test_distinct_values_report_nothing(self):
        self.assertEqual(MIGRATION._duplicated(["a", "b", "c"]), [])

    def test_empty_input_reports_nothing(self):
        self.assertEqual(MIGRATION._duplicated([]), [])

    def test_rows_without_a_barcode_are_not_counted_as_duplicates(self):
        from core.models import ResourceFile

        files = [ResourceFileFactory() for _ in range(3)]
        # NULL repeats freely under a unique constraint, so the query has to exclude it or
        # every pre-barcode file in the database would read as a conflict.
        ResourceFile.objects.filter(pk__in=[f.pk for f in files]).update(barcode=None)

        MIGRATION._reject_duplicate_barcodes(
            _historical_apps(), connection.schema_editor()
        )  # does not raise

    def test_the_real_query_passes_on_distinct_barcodes(self):
        ResourceFileFactory()
        ResourceFileFactory()

        MIGRATION._reject_duplicate_barcodes(
            _historical_apps(), connection.schema_editor()
        )  # does not raise
