"""The invariants that keep a blur's motion path consistent with its time window.

Enforced in BlurAnnotationPosition.save() and BlurAnnotation.reconcile_positions(). Between
them they cover GitHub issue #322's second and third items - updating the first position's
time when the item's left handle is dragged, and updating all positions when the item is
dragged along the timeline.
"""

from django.db.utils import IntegrityError
from django.test import TestCase

from core.factories import BlurAnnotationFactory
from core.factories import BlurAnnotationPositionFactory
from core.models import BLUR_MIN_HEIGHT
from core.models import BLUR_MIN_WIDTH


def _times(blur):
    return [position.time for position in blur.positions.all()]


class ReconcilePositionsTests(TestCase):
    """Moving or resizing a blur item on the timeline."""

    def setUp(self):
        self.blur = BlurAnnotationFactory(start_time=10.0, end_time=15.0)
        self.blur.positions.all().delete()
        # Deliberately distinct geometry per position so it is visible which row survived a
        # reconcile, not just how many did.
        for time, x in ((10.0, 10.0), (12.0, 20.0), (15.0, 30.0)):
            BlurAnnotationPositionFactory(
                blur_annotation=self.blur, time=time, x=x, y=5.0
            )

    def _move_to(self, start, end):
        old_start, old_end = self.blur.start_time, self.blur.end_time
        self.blur.start_time, self.blur.end_time = start, end
        self.blur.save()
        self.blur.reconcile_positions(old_start, old_end)

    def test_moving_the_item_shifts_every_position_by_the_same_delta(self):
        original_ids = [p.pk for p in self.blur.positions.all()]

        self._move_to(20.0, 25.0)

        self.assertEqual(_times(self.blur), [20.0, 22.0, 25.0])
        self.assertEqual(
            [p.pk for p in self.blur.positions.all()],
            original_ids,
            "a move should carry the same rows along, not delete and recreate them",
        )

    def test_moving_the_item_backwards_shifts_every_position(self):
        self._move_to(4.0, 9.0)
        self.assertEqual(_times(self.blur), [4.0, 6.0, 9.0])

    def test_moving_over_an_adjacent_position_does_not_violate_the_unique_index(self):
        """Shifting must not transiently collide with a position it is moving onto.

        The unique (blur_annotation, time) index is checked per row, so shifting +1s over
        positions at 10 and 11 fails with IntegrityError unless the later one moves first.
        """
        self.blur.positions.all().delete()
        for time in (10.0, 11.0):
            BlurAnnotationPositionFactory(blur_annotation=self.blur, time=time)
        self.blur.end_time = 11.0
        self.blur.save()

        self._move_to(11.0, 12.0)

        self.assertEqual(_times(self.blur), [11.0, 12.0])

    def test_dragging_the_left_handle_rightwards_repins_the_first_position(self):
        """#322 item 2. The first position's *time* moves, carrying the geometry that was
        showing at the new start rather than resetting to a default box."""
        self._move_to(13.0, 15.0)

        self.assertEqual(_times(self.blur), [13.0, 15.0])
        first = self.blur.positions.first()
        self.assertEqual(
            first.x, 20.0, "should carry the geometry of the position at time 12"
        )

    def test_dragging_the_left_handle_leftwards_extends_the_first_position(self):
        self._move_to(5.0, 15.0)

        self.assertEqual(_times(self.blur), [5.0, 12.0, 15.0])
        self.assertEqual(self.blur.positions.first().x, 10.0)

    def test_dragging_the_right_handle_prunes_trailing_positions(self):
        self._move_to(10.0, 11.0)

        self.assertEqual(_times(self.blur), [10.0])

    def test_reconcile_never_leaves_a_blur_without_a_position(self):
        """Even when the new window excludes every existing position, geometry survives."""
        self.blur.positions.exclude(time=10.0).delete()

        self._move_to(30.0, 31.0)

        self.assertEqual(_times(self.blur), [30.0])
        self.assertEqual(
            self.blur.positions.first().x,
            10.0,
            "should keep the author's geometry rather than substituting a default box",
        )

    def test_the_first_position_always_sits_at_start_time(self):
        for start, end in [
            (20.0, 25.0),  # move
            (18.0, 30.0),  # grow both edges
            (22.0, 24.0),  # shrink both edges
            (0.0, 24.0),  # extend to the very beginning
        ]:
            with self.subTest(start=start, end=end):
                self._move_to(start, end)
                self.assertEqual(self.blur.positions.first().time, start)

    def test_changing_only_the_track_leaves_positions_untouched(self):
        before = _times(self.blur)
        self.blur.reconcile_positions(self.blur.start_time, self.blur.end_time)
        self.assertEqual(_times(self.blur), before)


class EnsureFirstPositionTests(TestCase):
    def test_a_blur_with_no_positions_gets_a_default_at_start_time(self):
        blur = BlurAnnotationFactory(start_time=7.0, end_time=12.0)
        blur.positions.all().delete()

        created = blur.ensure_first_position()

        self.assertEqual(created.time, 7.0)
        self.assertEqual(_times(blur), [7.0])
        self.assertGreater(created.width, BLUR_MIN_WIDTH)
        self.assertGreater(created.height, BLUR_MIN_HEIGHT)

    def test_it_is_idempotent(self):
        blur = BlurAnnotationFactory(start_time=7.0, end_time=12.0)
        first = blur.ensure_first_position()
        again = blur.ensure_first_position()
        self.assertEqual(first.pk, again.pk)
        self.assertEqual(blur.positions.count(), 1)


class PositionInvariantTests(TestCase):
    def setUp(self):
        self.blur = BlurAnnotationFactory(start_time=0.0, end_time=20.0)
        self.blur.positions.all().delete()

    def _make(self, **kwargs):
        return BlurAnnotationPositionFactory(blur_annotation=self.blur, **kwargs)

    def test_duplicate_times_are_rejected(self):
        self._make(time=5.0)
        with self.assertRaises(IntegrityError):
            self._make(time=5.0)

    def test_times_are_quantized_so_near_duplicates_collide(self):
        self._make(time=5.001)
        self.assertEqual(_times(self.blur), [5.0])
        with self.assertRaises(IntegrityError):
            self._make(time=5.004)

    def test_positions_come_back_ordered_by_time(self):
        for time in (9.0, 3.0, 6.0):
            self._make(time=time)
        self.assertEqual(_times(self.blur), [3.0, 6.0, 9.0])

    def test_geometry_is_clamped_rather_than_rejected(self):
        position = self._make(time=1.0, x=-20.0, y=-5.0, width=200.0, height=400.0)
        self.assertEqual(
            (position.x, position.y, position.width, position.height),
            (0.0, 0.0, 100.0, 100.0),
        )

    def test_a_box_is_never_smaller_than_the_minimum(self):
        position = self._make(time=1.0, width=0.5, height=0.1)
        self.assertEqual(position.width, BLUR_MIN_WIDTH)
        self.assertEqual(position.height, BLUR_MIN_HEIGHT)

    def test_a_box_is_kept_inside_the_frame(self):
        position = self._make(time=1.0, x=95.0, y=95.0, width=20.0, height=20.0)
        self.assertLessEqual(position.x + position.width, 100.0)
        self.assertLessEqual(position.y + position.height, 100.0)

    def test_zero_x_and_y_are_preserved(self):
        """Flush against the left or top edge is a legitimate place for a blur."""
        position = self._make(time=1.0, x=0.0, y=0.0)
        self.assertEqual((position.x, position.y), (0.0, 0.0))

    def test_to_json_matches_the_player_payload(self):
        position = self._make(time=2.5)
        self.assertEqual(
            position.to_json(),
            self.blur.to_player_json()["positions"][0],
            "the editor's responses and the player payload must not drift apart",
        )
