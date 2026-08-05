"""Tests for the 0004 data migration that normalizes existing blur positions.

The migration's `_normalize` is driven directly rather than through the migration executor:
it is plain data manipulation over `apps.get_model`, and calling it lets each case be set up
and asserted precisely. `django.apps` is passed as the registry, which is equivalent here
because the fields it touches are unchanged by this migration.

The case that matters most is the "sentinel": every blur used to be seeded with a
`time=0, x=50, y=50, width=4, height=3` position regardless of its actual start_time, and
five code paths existed to keep that row alive and hidden. See the migration's docstring.
"""

import importlib

from django.apps import apps
from django.test import TestCase

from core.factories import BlurAnnotationFactory
from core.models import BlurAnnotationPosition

MIGRATION = importlib.import_module("core.migrations.0004_blur_position_invariants")
SENTINEL = {"x": 50.0, "y": 50.0, "width": 4.0, "height": 3.0}


def _normalize():
    MIGRATION._normalize(apps, None)


def _add(blur, time, **geometry):
    """Create a position bypassing the model's clamping/rounding.

    The migration exists to clean up rows written *before* those rules were enforced, so the
    fixtures have to be able to violate them.
    """
    fields = {"x": 10.0, "y": 20.0, "width": 30.0, "height": 40.0, **geometry}
    position = BlurAnnotationPosition(blur_annotation=blur, time=time, **fields)
    BlurAnnotationPosition.objects.bulk_create([position])
    return position


def _rows(blur):
    return [
        (p.time, p.x, p.y, p.width, p.height)
        for p in blur.positions.all().order_by("time")
    ]


class NormalizeBlurPositionsTests(TestCase):
    def setUp(self):
        self.blur = BlurAnnotationFactory(start_time=15.0, end_time=20.0)
        self.blur.positions.all().delete()

    def test_the_sentinel_is_dropped_when_real_positions_exist(self):
        _add(self.blur, 0.0, **SENTINEL)
        _add(self.blur, 17.5, x=11.0, y=21.0, width=31.0, height=41.0)
        _add(self.blur, 20.0, x=12.0, y=22.0, width=32.0, height=42.0)

        _normalize()

        self.assertEqual(
            _rows(self.blur),
            [
                (15.0, 11.0, 21.0, 31.0, 41.0),
                (20.0, 12.0, 22.0, 32.0, 42.0),
            ],
            "the sentinel should go, and the first real position move to start_time",
        )

    def test_a_lone_sentinel_is_repinned_rather_than_deleted(self):
        """With nothing else to fall back on, keep it - a blur with no geometry cannot render."""
        _add(self.blur, 0.0, **SENTINEL)

        _normalize()

        self.assertEqual(_rows(self.blur), [(15.0, 50.0, 50.0, 4.0, 3.0)])

    def test_a_user_positioned_row_at_time_zero_is_kept(self):
        """Only the exact seeded fingerprint counts as a sentinel; real geometry survives."""
        _add(self.blur, 0.0, x=1.0, y=2.0, width=33.0, height=44.0)
        _add(self.blur, 18.0, x=9.0, y=9.0)

        _normalize()

        self.assertEqual(
            _rows(self.blur),
            [(15.0, 1.0, 2.0, 33.0, 44.0), (18.0, 9.0, 9.0, 30.0, 40.0)],
        )

    def test_times_are_rounded_and_duplicates_collapse_to_the_newest_row(self):
        _add(self.blur, 15.0)
        older = _add(self.blur, 18.001, x=1.0)
        newer = _add(self.blur, 18.004, x=2.0)
        self.assertLess(older.pk, newer.pk)

        _normalize()

        self.assertEqual(
            _rows(self.blur),
            [(15.0, 10.0, 20.0, 30.0, 40.0), (18.0, 2.0, 20.0, 30.0, 40.0)],
            "the surviving row should be the most recent edit",
        )

    def test_positions_after_the_end_time_are_dropped(self):
        _add(self.blur, 15.0)
        _add(self.blur, 25.0)

        _normalize()

        self.assertEqual([time for time, *_ in _rows(self.blur)], [15.0])

    def test_only_the_latest_pre_start_position_survives(self):
        _add(self.blur, 2.0, x=1.0)
        _add(self.blur, 9.0, x=2.0)
        _add(self.blur, 18.0, x=3.0)

        _normalize()

        rows = _rows(self.blur)
        self.assertEqual([time for time, *_ in rows], [15.0, 18.0])
        self.assertEqual(
            rows[0][1], 2.0, "should carry the geometry showing at the new start"
        )

    def test_a_pre_start_row_collapses_into_an_existing_row_at_start_time(self):
        _add(self.blur, 9.0, x=1.0)
        _add(self.blur, 15.0, x=2.0)

        _normalize()

        self.assertEqual([time for time, *_ in _rows(self.blur)], [15.0])
        self.assertEqual(
            self.blur.positions.first().x, 2.0, "the row already at start_time wins"
        )

    def test_a_blur_with_no_positions_gets_a_default_at_start_time(self):
        _normalize()

        rows = _rows(self.blur)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][0], 15.0)

    def test_normalize_is_idempotent(self):
        _add(self.blur, 0.0, **SENTINEL)
        _add(self.blur, 17.5, x=11.0)
        _add(self.blur, 25.0)

        _normalize()
        once = _rows(self.blur)
        _normalize()

        self.assertEqual(_rows(self.blur), once)

    def test_every_blur_satisfies_the_invariants_afterwards(self):
        """Whatever the starting mess, the postconditions hold for all of them at once."""
        messy = BlurAnnotationFactory(start_time=3.0, end_time=8.0)
        messy.positions.all().delete()
        _add(messy, 0.0, **SENTINEL)
        _add(messy, 1.0)
        _add(messy, 4.0)
        _add(messy, 99.0)
        _add(self.blur, 0.0, **SENTINEL)
        _add(self.blur, 16.0)

        _normalize()

        for blur in (self.blur, messy):
            with self.subTest(blur=blur.pk):
                positions = list(blur.positions.all().order_by("time"))
                self.assertGreaterEqual(len(positions), 1)
                self.assertEqual(positions[0].time, blur.start_time)
                times = [p.time for p in positions]
                self.assertEqual(len(times), len(set(times)), "times must be unique")
                for position in positions:
                    self.assertGreaterEqual(position.time, blur.start_time)
                    self.assertLessEqual(position.time, blur.end_time)
