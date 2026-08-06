"""Tests for the 0004 data migration that normalizes existing blur positions.

The migration's `_normalize` is driven directly rather than through the migration executor: it
is plain data manipulation over `apps.get_model`, and calling it lets each case be set up and
asserted precisely.

The registry it is handed is a real historical one, rebuilt from the migration graph at 0004's
dependency. Passing `django.apps` instead is *not* equivalent, and the difference is not about
which fields exist: historical models carry no custom methods, so the live registry supplies a
BlurAnnotationPosition whose `save()` clamps and rounds geometry, while the one the migration
actually receives at runtime does not. Driving these tests through the live registry meant every
geometry assertion below was really testing `BlurAnnotationPosition.save()` - they would have
passed just as well if `_normalize` did no geometry work at all - and it introduced a failure
mode with no counterpart in production, where an intermediate `save(update_fields=["time"])`
silently clamped an instance in memory while persisting only its time.

The case that matters most is the "sentinel": every blur used to be seeded with a
`time=0, x=50, y=50, width=4, height=3` position regardless of its actual start_time, and
five code paths existed to keep that row alive and hidden. See the migration's docstring.
"""

import importlib

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase

from core.factories import BlurAnnotationFactory

MIGRATION = importlib.import_module("core.migrations.0004_blur_position_invariants")
SENTINEL = {"x": 50.0, "y": 50.0, "width": 4.0, "height": 3.0}

# The state *before* 0004 runs, which is exactly what RunPython is handed. Built once: it reads
# the migration graph off disk and touches no database rows, so it is safe to share.
_HISTORICAL_APPS = None


def _historical_apps():
    global _HISTORICAL_APPS
    if _HISTORICAL_APPS is None:
        loader = MigrationExecutor(connection).loader
        _HISTORICAL_APPS = loader.project_state(("core", "0003_language_bcp47")).apps
    return _HISTORICAL_APPS


def _normalize():
    MIGRATION._normalize(_historical_apps(), None)


def _add(blur, time, **geometry):
    """Create a position bypassing the model's clamping/rounding.

    The migration exists to clean up rows written *before* those rules were enforced, so the
    fixtures have to be able to violate them. The historical model is what makes that possible
    without any bulk_create trickery: it has no custom save(), so values land as given.
    """
    fields = {"x": 10.0, "y": 20.0, "width": 30.0, "height": 40.0, **geometry}
    position_model = _historical_apps().get_model("core", "BlurAnnotationPosition")
    return position_model.objects.create(
        blur_annotation_id=blur.pk, time=time, **fields
    )


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

    def test_a_lone_sentinel_is_kept_but_given_a_usable_box(self):
        """Keep the row - a blur with no geometry cannot render - but not its geometry.

        A sentinel is by definition a box the user never placed, and 4x3 of the frame is too
        small to see or grab. Preserving it would leave the blur as unusable after the migration
        as before it, so it gets the box a newly created blur gets.
        """
        _add(self.blur, 0.0, **SENTINEL)

        _normalize()

        self.assertEqual(_rows(self.blur), [(15.0, 40.0, 42.5, 20.0, 15.0)])

    def test_a_lone_sentinel_at_start_time_is_also_given_a_usable_box(self):
        """The sentinel's time coincides with start_time, so no repin happens to hang this off."""
        blur = BlurAnnotationFactory(start_time=0.0, end_time=6.0)
        blur.positions.all().delete()
        _add(blur, 0.0, **SENTINEL)

        _normalize()

        self.assertEqual(_rows(blur), [(0.0, 40.0, 42.5, 20.0, 15.0)])

    def test_a_sentinel_is_left_alone_when_the_user_positioned_other_points(self):
        """Only a *lone* sentinel is replaced; one kept as real geometry is not second-guessed."""
        blur = BlurAnnotationFactory(start_time=0.0, end_time=6.0)
        blur.positions.all().delete()
        _add(blur, 0.0, **SENTINEL)
        _add(blur, 4.0, x=9.0, y=9.0)

        _normalize()

        self.assertEqual(
            _rows(blur),
            [(0.0, 50.0, 50.0, 4.0, 4.0), (4.0, 9.0, 9.0, 30.0, 40.0)],
            "kept as-is apart from the height clamp every row gets",
        )

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

    # --- geometry ------------------------------------------------------------
    #
    # BlurAnnotationPosition.save() is the only thing enforcing these rules and there is no
    # database constraint behind it, so rows written before this migration can violate all of
    # them. Nothing on the read path corrects a stored box, which is why they are fixed here.

    def test_a_box_hanging_off_the_frame_is_pulled_back_on(self):
        _add(self.blur, 15.0, x=95.0, y=90.0, width=30.0, height=40.0)

        _normalize()

        self.assertEqual(_rows(self.blur), [(15.0, 70.0, 60.0, 30.0, 40.0)])

    def test_a_negative_origin_is_pulled_onto_the_frame(self):
        _add(self.blur, 15.0, x=-12.0, y=-3.0)

        _normalize()

        self.assertEqual(_rows(self.blur), [(15.0, 0.0, 0.0, 30.0, 40.0)])

    def test_a_box_below_the_minimum_size_is_grown_to_it(self):
        _add(self.blur, 15.0, x=10.0, y=20.0, width=0.5, height=1.0)

        _normalize()

        self.assertEqual(_rows(self.blur), [(15.0, 10.0, 20.0, 3.0, 4.0)])

    def test_an_oversized_box_is_capped_at_the_whole_frame(self):
        _add(self.blur, 15.0, x=40.0, y=40.0, width=250.0, height=180.0)

        _normalize()

        self.assertEqual(_rows(self.blur), [(15.0, 0.0, 0.0, 100.0, 100.0)])

    def test_float_noise_is_quantized_to_two_decimals(self):
        _add(self.blur, 15.0, x=10.123456, y=20.987654, width=30.5551, height=40.4449)

        _normalize()

        self.assertEqual(_rows(self.blur), [(15.0, 10.12, 20.99, 30.56, 40.44)])

    def test_geometry_that_already_obeys_the_rules_is_untouched(self):
        _add(self.blur, 15.0, x=12.5, y=30.0, width=22.0, height=14.0)

        _normalize()

        self.assertEqual(_rows(self.blur), [(15.0, 12.5, 30.0, 22.0, 14.0)])

    def test_clamping_reaches_a_position_that_is_not_the_first(self):
        """The loop covers every survivor, not just the row the repin touched."""
        _add(self.blur, 15.0)
        _add(self.blur, 18.0, x=99.0, y=99.0, width=30.0, height=40.0)

        _normalize()

        self.assertEqual(
            _rows(self.blur),
            [(15.0, 10.0, 20.0, 30.0, 40.0), (18.0, 70.0, 60.0, 30.0, 40.0)],
        )

    def test_normalize_is_idempotent(self):
        _add(self.blur, 0.0, **SENTINEL)
        _add(self.blur, 17.5, x=11.0)
        _add(self.blur, 25.0)

        _normalize()
        once = _rows(self.blur)
        _normalize()

        self.assertEqual(_rows(self.blur), once)

    def test_normalize_is_idempotent_over_a_lone_sentinel(self):
        """The replaced geometry must not read as a sentinel again on a second pass."""
        _add(self.blur, 0.0, **SENTINEL)

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
        _add(messy, 4.0, x=-40.0, y=140.0, width=0.2, height=500.0)
        _add(messy, 99.0)
        _add(self.blur, 0.0, **SENTINEL)
        _add(self.blur, 16.0, x=98.7654, y=1.0)

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
                    # The geometry half of the invariants: on the frame, big enough to grab,
                    # and stored at the precision the panel renders.
                    self.assertGreaterEqual(position.width, 3.0)
                    self.assertGreaterEqual(position.height, 4.0)
                    self.assertGreaterEqual(position.x, 0.0)
                    self.assertGreaterEqual(position.y, 0.0)
                    self.assertLessEqual(position.x + position.width, 100.0)
                    self.assertLessEqual(position.y + position.height, 100.0)
                    for field in ("x", "y", "width", "height"):
                        value = getattr(position, field)
                        self.assertEqual(value, round(value, 2), field)
