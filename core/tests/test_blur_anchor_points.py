"""The invariants that keep a blur's motion path consistent with its time window.

Enforced in BlurAnchorPoint.save() and BlurAnnotation.reconcile_blur_anchor_points(). Between
them they cover GitHub issue #322's second and third items - updating the first anchor point's
time when the item's left handle is dragged, and updating all anchor points when the item is
dragged along the timeline.
"""

import re

from django.db.utils import IntegrityError
from django.template.loader import render_to_string
from django.test import TestCase

from core.factories import BlurAnchorPointFactory
from core.factories import BlurAnnotationFactory
from core.models import BLUR_MIN_HEIGHT
from core.models import BLUR_MIN_WIDTH
from core.models import BlurAnchorPoint


def _times(blur):
    return [
        blur_anchor_point.time for blur_anchor_point in blur.blur_anchor_points.all()
    ]


def assert_blur_anchor_points_within_window(test, blur):
    """Every stored anchor point obeys start_time <= time <= end_time.

    Asserted after every reconcile rather than in the tests that happen to be about pruning,
    because an anchor point outside the window is not a pruning bug - it is geometry that
    geometry_at() will interpolate toward and playback can never reach.
    """
    for time in _times(blur):
        test.assertGreaterEqual(
            time,
            blur.start_time,
            f"blur_anchor_point at {time} precedes start_time {blur.start_time}",
        )
        test.assertLessEqual(
            time,
            blur.end_time,
            f"blur_anchor_point at {time} follows end_time {blur.end_time}",
        )


class ReconcileBlurAnchorPointsTests(TestCase):
    """Moving or resizing a blur item on the timeline."""

    def setUp(self):
        self.blur = BlurAnnotationFactory(start_time=10.0, end_time=15.0)
        self.blur.blur_anchor_points.all().delete()
        # Deliberately distinct geometry per anchor point so it is visible which row survived a
        # reconcile, not just how many did.
        for time, x in ((10.0, 10.0), (12.0, 20.0), (15.0, 30.0)):
            BlurAnchorPointFactory(blur_annotation=self.blur, time=time, x=x, y=5.0)

    def _move_to(self, start, end):
        old_start, old_end = self.blur.start_time, self.blur.end_time
        self.blur.start_time, self.blur.end_time = start, end
        self.blur.save()
        self.blur.reconcile_blur_anchor_points(old_start, old_end)
        # Checked here rather than per test, so every reconcile in this class asserts it.
        assert_blur_anchor_points_within_window(self, self.blur)

    def test_moving_the_item_shifts_every_blur_anchor_point_by_the_same_delta(self):
        original_ids = [p.pk for p in self.blur.blur_anchor_points.all()]

        self._move_to(20.0, 25.0)

        self.assertEqual(_times(self.blur), [20.0, 22.0, 25.0])
        self.assertEqual(
            [p.pk for p in self.blur.blur_anchor_points.all()],
            original_ids,
            "a move should carry the same rows along, not delete and recreate them",
        )

    def test_moving_the_item_backwards_shifts_every_blur_anchor_point(self):
        self._move_to(4.0, 9.0)
        self.assertEqual(_times(self.blur), [4.0, 6.0, 9.0])

    def test_moving_over_an_adjacent_blur_anchor_point_does_not_violate_the_unique_index(
        self,
    ):
        """Shifting must not transiently collide with an anchor point it is moving onto.

        The unique (blur_annotation, time) index is checked per row, so shifting +1s over
        anchor points at 10 and 11 fails with IntegrityError unless the later one moves first.
        """
        self.blur.blur_anchor_points.all().delete()
        for time in (10.0, 11.0):
            BlurAnchorPointFactory(blur_annotation=self.blur, time=time)
        self.blur.end_time = 11.0
        self.blur.save()

        self._move_to(11.0, 12.0)

        self.assertEqual(_times(self.blur), [11.0, 12.0])

    def test_dragging_the_left_handle_rightwards_repins_the_first_blur_anchor_point(
        self,
    ):
        """#322 item 2. The first anchor point's *time* moves, carrying the geometry that was
        showing at the new start rather than resetting to a default box.

        "Showing" means the interpolated rect, not the nearest stored anchor point: at t=13 the box
        was a third of the way from the one at 12 (x=20) to the one at 15 (x=30). Retiming the
        anchor point at 12 without re-deriving its geometry would leave the blur's first frames
        covering where the subject was a second earlier, which is exposure.
        """
        self._move_to(13.0, 15.0)

        self.assertEqual(_times(self.blur), [13.0, 15.0])
        first = self.blur.blur_anchor_points.first()
        self.assertAlmostEqual(
            first.x, 23.333, 2, "should carry the interpolated geometry at time 13"
        )

    def test_dragging_the_left_handle_leftwards_extends_the_first_blur_anchor_point(
        self,
    ):
        self._move_to(5.0, 15.0)

        self.assertEqual(_times(self.blur), [5.0, 12.0, 15.0])
        self.assertEqual(self.blur.blur_anchor_points.first().x, 10.0)

    def test_dragging_the_right_handle_prunes_trailing_blur_anchor_points(self):
        self._move_to(10.0, 11.0)

        self.assertEqual(_times(self.blur), [10.0])

    def test_reconcile_never_leaves_a_blur_without_a_blur_anchor_point(self):
        """Even when the new window excludes every existing anchor point, geometry survives."""
        self.blur.blur_anchor_points.exclude(time=10.0).delete()

        self._move_to(30.0, 31.0)

        self.assertEqual(_times(self.blur), [30.0])
        self.assertEqual(
            self.blur.blur_anchor_points.first().x,
            10.0,
            "should keep the author's geometry rather than substituting a default box",
        )

    def test_the_first_blur_anchor_point_always_sits_at_start_time(self):
        for start, end in [
            (20.0, 25.0),  # move
            (18.0, 30.0),  # grow both edges
            (22.0, 24.0),  # shrink both edges
            (0.0, 24.0),  # extend to the very beginning
        ]:
            with self.subTest(start=start, end=end):
                self._move_to(start, end)
                self.assertEqual(self.blur.blur_anchor_points.first().time, start)

    def test_changing_only_the_track_leaves_blur_anchor_points_untouched(self):
        before = _times(self.blur)
        self.blur.reconcile_blur_anchor_points(self.blur.start_time, self.blur.end_time)
        self.assertEqual(_times(self.blur), before)

    def test_a_start_only_edit_of_one_hundredth_does_not_push_a_blur_anchor_point_past_the_end(
        self,
    ):
        """The move/resize test tolerates 0.02s, which is looser than the 0.01s stored precision.

        So this start-only resize is classified as a move and every anchor point shifts by 0.01 -
        which without the unconditional window check at the end of reconcile_blur_anchor_points leaves
        the anchor point authored at 15.0 sitting at 15.01, past an end_time of 15.0.
        """
        self._move_to(10.01, 15.0)

        self.assertEqual(_times(self.blur), [10.01, 12.01])

    def test_a_right_handle_edit_of_one_hundredth_prunes_the_blur_anchor_point_it_excludes(
        self,
    ):
        """The mirror of the case above, which takes the resize branch instead because the start
        did not move. blurAnchorPointsLostByRetiming() has to agree that this loses a point, since the
        user is warned from the client but the deletion happens here.
        """
        self._move_to(10.0, 14.99)

        self.assertEqual(_times(self.blur), [10.0, 12.0])


class EnsureFirstBlurAnchorPointTests(TestCase):
    def test_a_blur_with_no_blur_anchor_points_gets_a_default_at_start_time(self):
        blur = BlurAnnotationFactory(start_time=7.0, end_time=12.0)
        blur.blur_anchor_points.all().delete()

        created = blur.ensure_first_blur_anchor_point()

        self.assertEqual(created.time, 7.0)
        self.assertEqual(_times(blur), [7.0])
        self.assertGreater(created.width, BLUR_MIN_WIDTH)
        self.assertGreater(created.height, BLUR_MIN_HEIGHT)

    def test_it_is_idempotent(self):
        blur = BlurAnnotationFactory(start_time=7.0, end_time=12.0)
        first = blur.ensure_first_blur_anchor_point()
        again = blur.ensure_first_blur_anchor_point()
        self.assertEqual(first.pk, again.pk)
        self.assertEqual(blur.blur_anchor_points.count(), 1)


class BlurAnchorPointInvariantTests(TestCase):
    def setUp(self):
        self.blur = BlurAnnotationFactory(start_time=0.0, end_time=20.0)
        self.blur.blur_anchor_points.all().delete()

    def _make(self, **kwargs):
        return BlurAnchorPointFactory(blur_annotation=self.blur, **kwargs)

    def test_duplicate_times_are_rejected(self):
        self._make(time=5.0)
        with self.assertRaises(IntegrityError):
            self._make(time=5.0)

    def test_times_are_quantized_so_near_duplicates_collide(self):
        self._make(time=5.001)
        self.assertEqual(_times(self.blur), [5.0])
        with self.assertRaises(IntegrityError):
            self._make(time=5.004)

    def test_blur_anchor_points_come_back_ordered_by_time(self):
        for time in (9.0, 3.0, 6.0):
            self._make(time=time)
        self.assertEqual(_times(self.blur), [3.0, 6.0, 9.0])

    def test_geometry_is_clamped_rather_than_rejected(self):
        blur_anchor_point = self._make(
            time=1.0, x=-20.0, y=-5.0, width=200.0, height=400.0
        )
        self.assertEqual(
            (
                blur_anchor_point.x,
                blur_anchor_point.y,
                blur_anchor_point.width,
                blur_anchor_point.height,
            ),
            (0.0, 0.0, 100.0, 100.0),
        )

    def test_a_box_is_never_smaller_than_the_minimum(self):
        blur_anchor_point = self._make(time=1.0, width=0.5, height=0.1)
        self.assertEqual(blur_anchor_point.width, BLUR_MIN_WIDTH)
        self.assertEqual(blur_anchor_point.height, BLUR_MIN_HEIGHT)

    def test_a_box_is_kept_inside_the_frame(self):
        blur_anchor_point = self._make(
            time=1.0, x=95.0, y=95.0, width=20.0, height=20.0
        )
        self.assertLessEqual(blur_anchor_point.x + blur_anchor_point.width, 100.0)
        self.assertLessEqual(blur_anchor_point.y + blur_anchor_point.height, 100.0)

    def test_zero_x_and_y_are_preserved(self):
        """Flush against the left or top edge is a legitimate place for a blur."""
        blur_anchor_point = self._make(time=1.0, x=0.0, y=0.0)
        self.assertEqual((blur_anchor_point.x, blur_anchor_point.y), (0.0, 0.0))

    def test_to_json_matches_the_player_payload(self):
        blur_anchor_point = self._make(time=2.5)
        self.assertEqual(
            blur_anchor_point.to_json(),
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
        self.blur.blur_anchor_points.all().delete()
        for time, x, y, width, height in (
            (2.0, 10.0, 20.0, 30.0, 40.0),
            (6.0, 50.0, 24.0, 22.0, 48.0),
        ):
            BlurAnchorPointFactory(
                blur_annotation=self.blur,
                time=time,
                x=x,
                y=y,
                width=width,
                height=height,
            )

    def test_no_blur_anchor_points_has_no_geometry(self):
        self.blur.blur_anchor_points.all().delete()
        self.assertIsNone(self.blur.geometry_at(3.0))

    def test_a_lone_blur_anchor_point_holds_at_every_time(self):
        self.blur.blur_anchor_points.filter(time=6.0).delete()
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

    def test_it_holds_constant_outside_the_first_and_last_blur_anchor_point(self):
        """Never extrapolate: a blur must not drift somewhere its author never put it."""
        first = {"x": 10.0, "y": 20.0, "width": 30.0, "height": 40.0}
        last = {"x": 50.0, "y": 24.0, "width": 22.0, "height": 48.0}
        self.assertEqual(self.blur.geometry_at(-5.0), first)
        self.assertEqual(self.blur.geometry_at(2.0), first)
        self.assertEqual(self.blur.geometry_at(6.0), last)
        self.assertEqual(self.blur.geometry_at(9999.0), last)

    def test_it_walks_past_intermediate_blur_anchor_points_to_the_right_bracket(self):
        BlurAnchorPointFactory(
            blur_annotation=self.blur, time=4.0, x=0.0, y=0.0, width=10.0, height=10.0
        )
        # Between the new anchor point at 4.0 and the one at 6.0, not the original 2.0-to-6.0 span.
        self.assertEqual(
            self.blur.geometry_at(5.0),
            {"x": 25.0, "y": 12.0, "width": 16.0, "height": 29.0},
        )


class RetimingBlurAnchorPointLossTests(TestCase):
    """What a retiming actually deletes, against what the browser promised it would.

    The editor warns before a retiming that will discard blur anchor points, and it has to decide that
    without asking the server - so blurAnchorPointsLostByRetiming in core/static/js/video-geometry.js
    predicts what reconcile_blur_anchor_points is about to do. Two implementations of one destructive
    decision in two languages is the duplication that drifts silently, and here it drifts into
    either a warning about nothing or a point deleted without being announced.

    CASES is deliberately the same table tests/js/blur-retiming.test.js runs, with the same case
    names. A case added to one belongs in the other.
    """

    # (name, point times, new window, points the browser predicts will be lost)
    CASES = [
        ("a pure move keeps every point", [10.0, 12.0, 15.0], (20.0, 25.0), 0),
        ("a move backward keeps every point", [10.0, 12.0, 15.0], (2.0, 7.0), 0),
        (
            "shrinking the right edge drops the trailing point",
            [10.0, 12.0, 15.0],
            (10.0, 13.0),
            1,
        ),
        (
            "shrinking the left edge keeps the last point before it",
            [10.0, 12.0, 15.0],
            (13.0, 15.0),
            1,
        ),
        (
            "shrinking the left edge onto an existing point drops the redundant one",
            [10.0, 12.0, 15.0],
            (12.0, 15.0),
            1,
        ),
        (
            "shrinking both edges drops one at each end but keeps a leading survivor",
            [10.0, 12.0, 15.0],
            (11.0, 13.0),
            1,
        ),
        (
            "several leading points collapse to one survivor",
            [10.0, 11.0, 12.0, 15.0],
            (13.0, 15.0),
            2,
        ),
        (
            "a start-only nudge inside the tolerance still shifts the last point out",
            [10.0, 12.0, 15.0],
            (10.01, 15.0),
            1,
        ),
        (
            "a duration change at the tolerance boundary reads as a move in both languages",
            [10.0, 12.0, 15.0],
            (10.02, 15.0),
            1,
        ),
        (
            "a duration change past the tolerance is a resize and keeps the survivor",
            [10.0, 12.0, 15.0],
            (10.03, 15.0),
            0,
        ),
        (
            "a window clear of every point loses the whole path",
            [10.0, 12.0, 15.0],
            (5.0, 8.0),
            3,
        ),
    ]

    def _reconcile(self, times, new_window):
        """Run one case, returning how many of the original points reconcile deleted.

        Counted as pks that disappeared rather than as a drop in the row count, because
        ensure_first_blur_anchor_point *creates* an anchor point when a retiming clears them all - which would
        otherwise net out and read as one fewer loss than the user suffered.
        """
        blur = BlurAnnotationFactory(start_time=10.0, end_time=15.0)
        blur.blur_anchor_points.all().delete()
        # Distinct geometry per point, so a case that keeps the wrong survivor is a visible
        # difference rather than an off-by-one in a count.
        for index, time in enumerate(times):
            BlurAnchorPointFactory(
                blur_annotation=blur, time=time, x=10.0 + index * 10, y=5.0
            )
        original = set(blur.blur_anchor_points.values_list("pk", flat=True))

        old_start, old_end = blur.start_time, blur.end_time
        blur.start_time, blur.end_time = new_window
        blur.save()
        blur.reconcile_blur_anchor_points(old_start, old_end)

        assert_blur_anchor_points_within_window(self, blur)
        surviving = set(blur.blur_anchor_points.values_list("pk", flat=True))
        return len(original - surviving)

    def test_the_browsers_prediction_matches_what_the_server_deletes(self):
        for name, times, new_window, predicted in self.CASES:
            with self.subTest(name):
                self.assertEqual(
                    self._reconcile(times, new_window),
                    predicted,
                    f"blurAnchorPointsLostByRetiming promises {predicted} for this case; "
                    "reconcile_blur_anchor_points disagrees, so the warning is wrong",
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

    def _blur_anchor_point(self, time=6.0, **geometry):
        blur_anchor_point = BlurAnchorPointFactory(
            blur_annotation=self.blur, time=time, **geometry
        )
        blur_anchor_point.refresh_from_db()
        return blur_anchor_point

    def test_geometry_is_stored_to_two_decimals(self):
        blur_anchor_point = self._blur_anchor_point(
            x=26.249999999999996, y=1 / 3 * 100, width=40.005, height=19.994
        )
        self.assertEqual(blur_anchor_point.x, 26.25)
        self.assertEqual(blur_anchor_point.y, 33.33)
        self.assertEqual(blur_anchor_point.height, 19.99)
        # Half-way values are whatever Python's banker's rounding does; what matters is 2dp.
        self.assertEqual(round(blur_anchor_point.width, 2), blur_anchor_point.width)

    def test_a_box_against_the_right_edge_still_lands_exactly_on_100(self):
        """Rounding must not leave a box a hundredth of a percent short of, or past, the edge.

        The invariant holds because x is clamped against the *already rounded* width, so both
        halves of the sum come from the same grid.
        """
        blur_anchor_point = self._blur_anchor_point(
            x=99.0, y=0.0, width=30.33, height=10.0
        )
        self.assertEqual(blur_anchor_point.x, 69.67)
        self.assertEqual(blur_anchor_point.x + blur_anchor_point.width, 100.0)

    def test_rounding_never_pushes_a_box_off_the_frame(self):
        # A distinct time each, because one blur cannot hold two points at the same instant.
        for offset, width in enumerate((30.33, 33.333333, 66.666666, 7.77, 0.005)):
            with self.subTest(width=width):
                blur_anchor_point = self._blur_anchor_point(
                    time=6.0 + offset, x=100.0, y=100.0, width=width, height=width
                )
                self.assertLessEqual(
                    blur_anchor_point.x + blur_anchor_point.width, 100.0
                )
                self.assertLessEqual(
                    blur_anchor_point.y + blur_anchor_point.height, 100.0
                )

    def test_the_minimums_are_not_rounded_away(self):
        blur_anchor_point = self._blur_anchor_point(
            x=0.0, y=0.0, width=0.001, height=0.001
        )
        self.assertEqual(blur_anchor_point.width, BLUR_MIN_WIDTH)
        self.assertEqual(blur_anchor_point.height, BLUR_MIN_HEIGHT)


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
        self.blur_anchor_point = BlurAnchorPointFactory(
            blur_annotation=self.blur, time=6.0
        )
        BlurAnchorPoint.objects.filter(pk=self.blur_anchor_point.pk).update(
            x=26.249999999999996,
            y=33.33333333333333,
            width=40.005000000000003,
            height=19.994999999999997,
        )

    def _rendered_values(self):
        html = render_to_string(
            "core/partials/blur_anchor_points.html",
            {"item_blur_anchor_points": self.blur.blur_anchor_points.all()},
        )
        # Matched across the whole tag because the formatter is free to wrap attributes.
        return re.findall(
            r'<input\s[^>]*class="blur-anchor-point-[a-z]+-input"[^>]*\svalue="([^"]*)"',
            html,
            re.DOTALL,
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
            self._rendered_values(),
            ["0:00:06.00", "26.25", "33.33", "40.01", "19.99"],
        )
