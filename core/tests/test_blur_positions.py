"""The invariants that keep a blur's motion path consistent with its time window.

Enforced in BlurAnnotationPosition.save() and BlurAnnotation.reconcile_positions(). Between
them they cover GitHub issue #322's second and third items - updating the first position's
time when the item's left handle is dragged, and updating all positions when the item is
dragged along the timeline.
"""

import re

from django.db.utils import IntegrityError
from django.template.loader import render_to_string
from django.test import TestCase

from core.factories import BlurAnnotationFactory
from core.factories import BlurAnnotationPositionFactory
from core.models import BLUR_MIN_HEIGHT
from core.models import BLUR_MIN_WIDTH
from core.models import BlurAnnotationPosition


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
        showing at the new start rather than resetting to a default box.

        "Showing" means the interpolated rect, not the nearest stored position: at t=13 the box
        was a third of the way from the one at 12 (x=20) to the one at 15 (x=30). Retiming the
        position at 12 without re-deriving its geometry would leave the blur's first frames
        covering where the subject was a second earlier, which is exposure.
        """
        self._move_to(13.0, 15.0)

        self.assertEqual(_times(self.blur), [13.0, 15.0])
        first = self.blur.positions.first()
        self.assertAlmostEqual(
            first.x, 23.333, 2, "should carry the interpolated geometry at time 13"
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


class GeometryAtTests(TestCase):
    """BlurAnnotation.geometry_at, the server's copy of the browser's rectAtTime.

    The case table is deliberately the same one tests/js/video-geometry.test.js uses, because two
    implementations of interpolation in two languages is exactly the kind of duplication that
    drifts silently. Asymmetric on every axis so an x/y or width/height mix-up cannot cancel out.
    """

    def setUp(self):
        self.blur = BlurAnnotationFactory(start_time=2.0, end_time=6.0)
        self.blur.positions.all().delete()
        for time, x, y, width, height in (
            (2.0, 10.0, 20.0, 30.0, 40.0),
            (6.0, 50.0, 24.0, 22.0, 48.0),
        ):
            BlurAnnotationPositionFactory(
                blur_annotation=self.blur,
                time=time,
                x=x,
                y=y,
                width=width,
                height=height,
            )

    def test_no_positions_has_no_geometry(self):
        self.blur.positions.all().delete()
        self.assertIsNone(self.blur.geometry_at(3.0))

    def test_a_lone_position_holds_at_every_time(self):
        self.blur.positions.filter(time=6.0).delete()
        expected = {"x": 10.0, "y": 20.0, "width": 30.0, "height": 40.0}
        for time in (-100.0, 0.0, 2.0, 1000.0):
            self.assertEqual(self.blur.geometry_at(time), expected)

    def test_every_field_interpolates_independently_at_the_midpoint(self):
        self.assertEqual(
            self.blur.geometry_at(4.0),
            {"x": 30.0, "y": 22.0, "width": 26.0, "height": 44.0},
        )

    def test_off_center_interpolation(self):
        self.assertEqual(
            self.blur.geometry_at(3.0),
            {"x": 20.0, "y": 21.0, "width": 28.0, "height": 42.0},
        )

    def test_it_holds_constant_outside_the_first_and_last_position(self):
        """Never extrapolate: a blur must not drift somewhere its author never put it."""
        first = {"x": 10.0, "y": 20.0, "width": 30.0, "height": 40.0}
        last = {"x": 50.0, "y": 24.0, "width": 22.0, "height": 48.0}
        self.assertEqual(self.blur.geometry_at(-5.0), first)
        self.assertEqual(self.blur.geometry_at(2.0), first)
        self.assertEqual(self.blur.geometry_at(6.0), last)
        self.assertEqual(self.blur.geometry_at(9999.0), last)

    def test_it_walks_past_intermediate_positions_to_the_right_bracket(self):
        BlurAnnotationPositionFactory(
            blur_annotation=self.blur, time=4.0, x=0.0, y=0.0, width=10.0, height=10.0
        )
        # Between the new position at 4.0 and the one at 6.0, not the original 2.0-to-6.0 span.
        self.assertEqual(
            self.blur.geometry_at(5.0),
            {"x": 25.0, "y": 12.0, "width": 16.0, "height": 29.0},
        )


class GeometryPrecisionTests(TestCase):
    """Geometry is rounded to 2dp on the way in, like time already was.

    A hundredth of a percent is well under a pixel on any display, so the digits past it carry no
    information - they are float noise from dividing pixels by a frame width. They were not
    harmless, though: the points panel puts all five numbers in front of the user, where
    `26.249999999999996` is clutter that reads like precision.
    """

    def setUp(self):
        self.blur = BlurAnnotationFactory(start_time=5.0, end_time=12.0)

    def _position(self, time=6.0, **geometry):
        position = BlurAnnotationPositionFactory(
            blur_annotation=self.blur, time=time, **geometry
        )
        position.refresh_from_db()
        return position

    def test_geometry_is_stored_to_two_decimals(self):
        position = self._position(
            x=26.249999999999996, y=1 / 3 * 100, width=40.005, height=19.994
        )
        self.assertEqual(position.x, 26.25)
        self.assertEqual(position.y, 33.33)
        self.assertEqual(position.height, 19.99)
        # Half-way values are whatever Python's banker's rounding does; what matters is 2dp.
        self.assertEqual(round(position.width, 2), position.width)

    def test_a_box_against_the_right_edge_still_lands_exactly_on_100(self):
        """Rounding must not leave a box a hundredth of a percent short of, or past, the edge.

        The invariant holds because x is clamped against the *already rounded* width, so both
        halves of the sum come from the same grid.
        """
        position = self._position(x=99.0, y=0.0, width=30.33, height=10.0)
        self.assertEqual(position.x, 69.67)
        self.assertEqual(position.x + position.width, 100.0)

    def test_rounding_never_pushes_a_box_off_the_frame(self):
        # A distinct time each, because one blur cannot hold two points at the same instant.
        for offset, width in enumerate((30.33, 33.333333, 66.666666, 7.77, 0.005)):
            with self.subTest(width=width):
                position = self._position(
                    time=6.0 + offset, x=100.0, y=100.0, width=width, height=width
                )
                self.assertLessEqual(position.x + position.width, 100.0)
                self.assertLessEqual(position.y + position.height, 100.0)

    def test_the_minimums_are_not_rounded_away(self):
        position = self._position(x=0.0, y=0.0, width=0.001, height=0.001)
        self.assertEqual(position.width, BLUR_MIN_WIDTH)
        self.assertEqual(position.height, BLUR_MIN_HEIGHT)


class PanelRenderingTests(TestCase):
    """The points panel renders at 2dp even for rows written before save() rounded.

    Every row created from now on is already 2dp, so `floatformat:2` in the template looks
    redundant against fresh data - and is not. Blurs imported from the legacy app, and every row
    written before this change, hold values like 26.249999999999996, and the panel is where a user
    reads them. queryset.update() is how that state is reachable at all: it bypasses save(), which
    is the only place the rounding happens.
    """

    def setUp(self):
        self.blur = BlurAnnotationFactory(start_time=5.0, end_time=12.0)
        self.position = BlurAnnotationPositionFactory(
            blur_annotation=self.blur, time=6.0
        )
        BlurAnnotationPosition.objects.filter(pk=self.position.pk).update(
            x=26.249999999999996,
            y=33.33333333333333,
            width=40.005000000000003,
            height=19.994999999999997,
        )

    def _rendered_values(self):
        html = render_to_string(
            "core/partials/blur_positions.html",
            {"item_positions": self.blur.positions.all()},
        )
        return re.findall(
            r'class="position-[a-z]+-input" type="text" value="([^"]*)"', html
        )

    def test_no_field_is_rendered_beyond_two_decimals(self):
        values = self._rendered_values()
        self.assertEqual(len(values), 5, values)
        for value in values:
            _, _, decimals = value.partition(".")
            self.assertLessEqual(
                len(decimals), 2, f"{value!r} is rendered to {len(decimals)} decimals"
            )

    def test_the_rendered_values_are_the_stored_ones_rounded(self):
        # Not blanked, not truncated to integers: the panel is a view onto the real geometry.
        self.assertEqual(
            self._rendered_values(), ["6.00", "26.25", "33.33", "40.01", "19.99"]
        )
