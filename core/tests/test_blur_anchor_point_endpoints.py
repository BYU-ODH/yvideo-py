"""Authorization and input-handling tests for the blur-anchor-point endpoints.

These endpoints previously had no authorization at all - unlike every neighbouring
annotation view - so any authenticated user could create, move, or delete blur anchor points on
any BlurAnnotation by guessing a numeric id. `delete_blur_anchor_point` additionally carried no
method decorator (so it answered GET) and dereferenced a possibly-unbound local on a failed
lookup, turning a bad id into a 500. Blurs cover copyrighted and explicit content, so these
are the tests that keep that hole closed.

The create/update split they were written against is now a single upsert, because the editor
cannot tell the two apart: a drag means "the blur belongs here at the time I am looking at", and
whether that is a new point is a fact about stored data. The upsert semantics themselves are
covered below, since getting them wrong retimes a point the user did not touch.
"""

import json

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from core.factories import AnnotationSetFactory
from core.factories import BlurAnchorPointFactory
from core.factories import BlurAnnotationFactory
from core.factories import PlaylistFactory
from core.factories import PlaylistUserAccessFactory
from core.factories import TrackFactory
from core.factories import UserFactory
from core.models import BlurAnchorPoint
from core.models import PlaylistRole
from core.tests.test_js_constant_parity import read_js_constant
from core.views_video_editor import BLUR_ANCHOR_POINT_FIRST_UNDELETABLE
from core.views_video_editor import BLUR_ANCHOR_POINT_FORBIDDEN
from core.views_video_editor import BLUR_ANCHOR_POINT_TIME_TAKEN


class BlurAnchorPointEndpointTests(TestCase):
    def setUp(self):
        self.owner = UserFactory(instructor=True)
        self.stranger = UserFactory(instructor=True)
        self.annotation_set = AnnotationSetFactory(owner=self.owner)
        self.track = TrackFactory(annotation_set=self.annotation_set)
        self.blur = BlurAnnotationFactory(
            track=self.track, start_time=5.0, end_time=12.0
        )
        self.blur_anchor_point = BlurAnchorPointFactory(
            blur_annotation=self.blur, time=5.0
        )

    def _upsert(self, **overrides):
        payload = {
            "time": 7.0,
            "x": 10.0,
            "y": 20.0,
            "width": 30.0,
            "height": 40.0,
        }
        payload.update(overrides)
        return self.client.post(
            reverse("upsert_blur_anchor_point", args=[self.blur.pk]),
            data=json.dumps(payload),
            content_type="application/json",
        )

    # --- authorization -------------------------------------------------------

    def test_upsert_requires_edit_permission(self):
        self.client.force_login(self.stranger)
        response = self._upsert()
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.content.decode(), BLUR_ANCHOR_POINT_FORBIDDEN)
        self.assertEqual(self.blur.blur_anchor_points.count(), 1)

    # can_edit is checked on the annotation from the URL before blur_anchor_point_id is looked at, so the
    # named path cannot be authorized differently from the unnamed one. What a blur_anchor_point_id *can* do -
    # reach across to another blur - is test_a_blur_anchor_point_id_from_another_blur_is_not_a_way_in below.

    def test_delete_requires_edit_permission(self):
        deletable = BlurAnchorPointFactory(blur_annotation=self.blur, time=8.0)
        self.client.force_login(self.stranger)
        response = self.client.delete(
            reverse("delete_blur_anchor_point", args=[deletable.pk])
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.content.decode(), BLUR_ANCHOR_POINT_FORBIDDEN)
        self.assertTrue(BlurAnchorPoint.objects.filter(pk=deletable.pk).exists())

    def test_a_ta_on_one_of_the_owners_playlists_may_edit(self):
        """Edit rights are derived from playlist roles rather than stored per set."""
        PlaylistUserAccessFactory(
            playlist=PlaylistFactory(owner=self.owner),
            user=self.stranger,
            playlist_role=PlaylistRole.COLLABORATOR,
        )
        self.client.force_login(self.stranger)
        self.assertEqual(self._upsert().status_code, 200)

    def test_a_blur_anchor_point_id_from_another_blur_is_not_a_way_in(self):
        """The id is scoped to the annotation in the URL, which is the one authorized above."""
        other_blur = BlurAnnotationFactory(
            track=TrackFactory(annotation_set=AnnotationSetFactory()),
            start_time=0.0,
            end_time=4.0,
        )
        foreign = BlurAnchorPointFactory(blur_annotation=other_blur, time=1.0)
        self.client.force_login(self.owner)
        response = self._upsert(blur_anchor_point_id=foreign.pk)
        self.assertEqual(response.status_code, 404)
        foreign.refresh_from_db()
        self.assertNotEqual(foreign.x, 10.0)

    def test_a_deleted_blur_takes_no_more_points(self):
        """`active=False` is what a deleted annotation looks like, and undo() can restore it.

        A write accepted in between would bring it back carrying points nobody placed
        deliberately. Every neighbouring editor view filters on `active`; these now do too.
        """
        self.blur.delete_with_history()
        self.client.force_login(self.owner)

        self.assertEqual(self._upsert(time=9.0).status_code, 404)
        self.assertEqual([p.time for p in self.blur.blur_anchor_points.all()], [5.0])

    def test_a_point_on_a_deleted_blur_cannot_be_deleted_either(self):
        deletable = BlurAnchorPointFactory(blur_annotation=self.blur, time=8.0)
        self.blur.delete_with_history()
        self.client.force_login(self.owner)

        response = self.client.delete(
            reverse("delete_blur_anchor_point", args=[deletable.pk])
        )
        self.assertEqual(response.status_code, 404)
        self.assertTrue(BlurAnchorPoint.objects.filter(pk=deletable.pk).exists())

    # --- upsert semantics ----------------------------------------------------

    def test_a_write_at_a_new_time_adds_a_point(self):
        """The headline fix: a drag at a time with no point must not retime an existing one."""
        self.client.force_login(self.owner)
        self.assertEqual(self._upsert(time=9.0).status_code, 200)

        self.assertEqual(
            [p.time for p in self.blur.blur_anchor_points.all()], [5.0, 9.0]
        )
        self.blur_anchor_point.refresh_from_db()
        self.assertEqual(
            self.blur_anchor_point.time, 5.0, "the existing point was retimed"
        )

    def test_a_write_near_an_existing_point_moves_that_point_without_retiming_it(self):
        """Within the snap window the user is editing that point, not making a new one."""
        BlurAnchorPointFactory(blur_annotation=self.blur, time=9.02, x=1.0)
        self.client.force_login(self.owner)
        self.assertEqual(self._upsert(time=9.0).status_code, 200)

        self.assertEqual(
            [p.time for p in self.blur.blur_anchor_points.all()], [5.0, 9.02]
        )
        self.assertEqual(self.blur.blur_anchor_points.get(time=9.02).x, 10.0)

    def test_a_write_between_two_close_points_lands_on_the_nearer_one(self):
        """With two points inside the snap window, the earlier one is not automatically it.

        Anchor points are ordered by time, so taking the first match here picked the lowest time in
        the window while the editor's own _blurAnchorPointAt picks the closest. The user dragged the box at
        9.03, the geometry was filed under 9.00, and the response said so - the box sprang back
        and a point they were not looking at had moved. Two points this close are reachable
        through the panel's time field, which accepts any value.
        """
        near = BlurAnchorPointFactory(blur_annotation=self.blur, time=9.03, x=1.0)
        far = BlurAnchorPointFactory(blur_annotation=self.blur, time=9.0, x=2.0)
        self.client.force_login(self.owner)

        payload = self._upsert(time=9.03).json()

        near.refresh_from_db()
        far.refresh_from_db()
        self.assertEqual(near.x, 10.0, "the nearest point should have taken the write")
        self.assertEqual(far.x, 2.0, "the further point must not be touched")
        self.assertEqual(payload["time"], 9.03)
        self.assertFalse(payload["created"])

    def test_the_nearer_point_wins_from_either_side(self):
        """The same, approaching from below, so the fix cannot be an ordering coincidence."""
        far = BlurAnchorPointFactory(blur_annotation=self.blur, time=9.04, x=2.0)
        near = BlurAnchorPointFactory(blur_annotation=self.blur, time=9.0, x=1.0)
        self.client.force_login(self.owner)

        payload = self._upsert(time=9.01).json()

        near.refresh_from_db()
        far.refresh_from_db()
        self.assertEqual(near.x, 10.0)
        self.assertEqual(far.x, 2.0)
        self.assertEqual(payload["time"], 9.0)

    def test_a_named_blur_anchor_point_can_be_retimed(self):
        """What the panel's time input and dragging a timeline dot need."""
        moving = BlurAnchorPointFactory(blur_annotation=self.blur, time=9.0)
        self.client.force_login(self.owner)
        self.assertEqual(
            self._upsert(blur_anchor_point_id=moving.pk, time=11.0).status_code, 200
        )
        self.assertEqual(
            [p.time for p in self.blur.blur_anchor_points.all()], [5.0, 11.0]
        )

    def test_retiming_onto_another_point_is_a_conflict_not_a_500(self):
        occupied = BlurAnchorPointFactory(blur_annotation=self.blur, time=9.0)
        BlurAnchorPointFactory(blur_annotation=self.blur, time=11.0)
        self.client.force_login(self.owner)
        response = self._upsert(blur_anchor_point_id=occupied.pk, time=11.0)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.content.decode(), BLUR_ANCHOR_POINT_TIME_TAKEN)
        # The rejected save must not have poisoned the transaction, and nothing may be lost.
        self.assertEqual(
            [p.time for p in self.blur.blur_anchor_points.all()], [5.0, 9.0, 11.0]
        )

    def test_a_time_outside_the_blurs_window_is_clamped_not_rejected(self):
        """Rounding at a boundary is not worth failing an edit over."""
        self.client.force_login(self.owner)
        self.assertEqual(self._upsert(time=99.0).status_code, 200)
        self.assertEqual(
            [p.time for p in self.blur.blur_anchor_points.all()], [5.0, 12.0]
        )

    def test_the_first_point_stays_pinned_to_the_blurs_start(self):
        """Invariant I5, re-asserted after every write rather than trusted to callers."""
        self.client.force_login(self.owner)
        self._upsert(time=8.0)
        self._upsert(blur_anchor_point_id=self.blur_anchor_point.pk, time=10.0)
        self.assertEqual(
            self.blur.blur_anchor_points.first().time, self.blur.start_time
        )

    def test_the_response_carries_the_blur_anchor_points_the_player_needs(self):
        self.client.force_login(self.owner)
        payload = self._upsert(time=9.0).json()
        self.assertEqual(
            [
                blur_anchor_point["time"]
                for blur_anchor_point in payload["blurAnchorPoints"]
            ],
            [5.0, 9.0],
        )
        # The panel and the timeline bar are both rebuilt from the response, so the editor never
        # has to reconstruct row numbering or dot placement itself.
        self.assertIn("blurAnchorPointsHtml", payload)
        self.assertIn("trackItem", payload)

    def test_the_returned_markup_carries_the_endpoints_for_the_next_write(self):
        """The editor reads its URLs out of this markup instead of assembling paths.

        BlurEditor posts to the bar's data-blur-anchor-points-url and deletes through each point's
        data-delete-url, so reverse() is the only thing that knows these routes and renaming one in
        core/urls.py travels with them. Asserting the reversed URLs rather than just the presence of
        the attributes is what makes this fail if a template starts hardcoding them again.
        """
        self.client.force_login(self.owner)
        deletable = BlurAnchorPointFactory(blur_annotation=self.blur, time=8.0)
        payload = self._upsert(time=9.0).json()

        upsert_url = reverse("upsert_blur_anchor_point", args=[self.blur.pk])
        self.assertIn(
            f'data-blur-anchor-points-url="{upsert_url}"', payload["trackItem"]
        )

        # The panel offers the button and the bar offers the dot; both are ways to delete this point,
        # so both carry its endpoint.
        delete_url = reverse("delete_blur_anchor_point", args=[deletable.pk])
        self.assertIn(
            f'data-delete-url="{delete_url}"', payload["blurAnchorPointsHtml"]
        )
        self.assertIn(f'data-delete-url="{delete_url}"', payload["trackItem"])

    def test_the_undeletable_first_point_is_offered_no_way_to_delete_it(self):
        """A control the server would refuse is not rendered, so its URL is not either.

        The first point's row has no delete button and the bar draws no dot for it - putting the
        endpoint on those controls rather than on the row is what keeps that true without a second
        rule saying which rows may use it.
        """
        self.client.force_login(self.owner)
        BlurAnchorPointFactory(blur_annotation=self.blur, time=8.0)
        payload = self._upsert(time=9.0).json()

        first_delete_url = reverse(
            "delete_blur_anchor_point", args=[self.blur_anchor_point.pk]
        )
        self.assertNotIn(first_delete_url, payload["blurAnchorPointsHtml"])
        self.assertNotIn(first_delete_url, payload["trackItem"])

    def test_a_refusal_says_something_the_editor_can_show_the_user(self):
        """The response body *is* the message; see serverMessage in BlurEditor.js.

        The client shows the body verbatim for a 4xx rather than keeping its own copy of the wording.
        But it only does that for a body that looks like a message - non-empty, not markup, and short
        enough for a status line - and falls back to a generic "could not be saved" otherwise. So
        these are the properties that decide whether the wording above ever reaches anyone.
        """
        cap = read_js_constant(
            "core/static/js/BlurEditor.js", "MAX_SERVER_MESSAGE_LENGTH"
        )
        self.assertIsNotNone(cap, "serverMessage no longer declares a length cap")
        for message in (
            BLUR_ANCHOR_POINT_FORBIDDEN,
            BLUR_ANCHOR_POINT_TIME_TAKEN,
            BLUR_ANCHOR_POINT_FIRST_UNDELETABLE,
        ):
            with self.subTest(message=message):
                self.assertTrue(message.strip(), "an empty body says nothing")
                self.assertFalse(
                    message.lstrip().startswith("<"),
                    "a body starting with markup is read as an error page, not a message",
                )
                self.assertLessEqual(len(message), cap)

    def test_the_three_projections_share_one_read_of_the_blur_anchor_points(self):
        """The response describes the same points three ways; it should not fetch them three times.

        generate_blur_item_and_anchor_points_html prefetches for exactly this reason, and the saving is
        invisible from the response - so without an assertion here, dropping the prefetch (or adding
        a fourth projection that calls anchor points.all() again) is a silent regression.

        Counts the reads of the anchor points table specifically rather than the whole request, so
        session and permission queries cannot drift the number and turn this into the test that
        fails for unrelated reasons.
        """
        self.client.force_login(self.owner)
        BlurAnchorPointFactory(blur_annotation=self.blur, time=8.0)

        with CaptureQueriesContext(connection) as queries:
            self._upsert(time=9.5)

        selects = [
            query["sql"]
            for query in queries.captured_queries
            if "core_bluranchorpoint" in query["sql"]
            and query["sql"].lstrip().upper().startswith("SELECT")
        ]
        # The write path legitimately reads the table before rendering: the upsert looks for a point
        # to land on, ensure_first_blur_anchor_point checks the earliest, and the response reads back the
        # stored time. What must not appear is one read per projection of the response.
        self.assertLessEqual(
            len(selects),
            4,
            "the blur_anchor_points table is being read once per projection of the response; "
            "generate_blur_item_and_anchor_points_html prefetches so they can share one read:\n"
            + "\n".join(selects),
        )

    def test_the_response_says_whether_a_point_was_added_or_moved(self):
        """Only this side can tell the two apart, and the editor has to say which happened.

        The client deliberately never sends an anchor point id from a drag, so it cannot know whether
        its write created a row or landed on one - that depends on where the stored points are.
        """
        self.client.force_login(self.owner)
        self.assertIs(self._upsert(time=9.0).json()["created"], True)
        self.assertIs(self._upsert(time=9.0, x=44.0).json()["created"], False)

    def test_the_response_reports_the_time_the_write_actually_landed_on(self):
        """Not the time that was requested: it is clamped into the window and snapped onto a point.

        The editor puts this number in front of the user ("Anchor point added at 0:00:12.00"), so reporting
        the request back would tell them the write went somewhere it did not.
        """
        self.client.force_login(self.owner)
        # Past end_time, so it clamps to 12.0.
        self.assertEqual(self._upsert(time=30.0).json()["time"], 12.0)
        # Within BLUR_SNAP_SECONDS of the existing point at 5.0, so it lands on that point.
        payload = self._upsert(time=5.03).json()
        self.assertEqual(payload["time"], 5.0)
        self.assertIs(payload["created"], False)

    # --- input handling ------------------------------------------------------

    def test_zero_x_and_y_are_accepted(self):
        """A box flush against the left or top edge is legitimate, not a bad request.

        These were previously rejected by a falsy check that could not distinguish 0 from
        a missing value.
        """
        self.client.force_login(self.owner)
        response = self._upsert(x=0, y=0)
        self.assertEqual(response.status_code, 200)
        created = self.blur.blur_anchor_points.get(time=7.0)
        self.assertEqual((created.x, created.y), (0.0, 0.0))

    def test_missing_field_is_still_a_bad_request(self):
        self.client.force_login(self.owner)
        self.assertEqual(self._upsert(x=None).status_code, 400)
        self.assertEqual(self._upsert(width="wide").status_code, 400)

    def test_upsert_with_an_unknown_annotation_is_404(self):
        self.client.force_login(self.owner)
        response = self.client.post(
            reverse("upsert_blur_anchor_point", args=[self.blur.pk + 10_000]),
            data=json.dumps({"time": 7.0, "x": 1, "y": 2, "width": 3, "height": 4}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 404)

    # --- delete --------------------------------------------------------------

    def test_delete_removes_the_point_and_returns_the_rebuilt_panel(self):
        deletable = BlurAnchorPointFactory(blur_annotation=self.blur, time=8.0)
        self.client.force_login(self.owner)
        response = self.client.delete(
            reverse("delete_blur_anchor_point", args=[deletable.pk])
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual([p.time for p in self.blur.blur_anchor_points.all()], [5.0])
        self.assertEqual(
            [p["time"] for p in response.json()["blurAnchorPoints"]], [5.0]
        )

    def test_the_first_point_cannot_be_deleted(self):
        """A blur with no anchor points has no geometry at all and silently stops rendering."""
        self.client.force_login(self.owner)
        response = self.client.delete(
            reverse("delete_blur_anchor_point", args=[self.blur_anchor_point.pk])
        )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.content.decode(), BLUR_ANCHOR_POINT_FIRST_UNDELETABLE)
        self.assertTrue(
            BlurAnchorPoint.objects.filter(pk=self.blur_anchor_point.pk).exists()
        )

    def test_delete_with_unknown_id_is_404_not_500(self):
        self.client.force_login(self.owner)
        response = self.client.delete(
            reverse(
                "delete_blur_anchor_point", args=[self.blur_anchor_point.pk + 10_000]
            )
        )
        self.assertEqual(response.status_code, 404)

    def test_delete_rejects_get(self):
        """The view is DELETE-only; it used to carry no method decorator at all.

        Logged in with an explicitly non-OIDC backend because mozilla_django_oidc's
        SessionRefresh middleware intercepts GET (and only GET) requests to bounce them to
        the SSO endpoint, which would mask the 405.
        """
        self.client.force_login(
            self.owner, backend="django.contrib.auth.backends.ModelBackend"
        )
        response = self.client.get(
            reverse("delete_blur_anchor_point", args=[self.blur_anchor_point.pk])
        )
        self.assertEqual(response.status_code, 405)
        self.assertTrue(
            BlurAnchorPoint.objects.filter(pk=self.blur_anchor_point.pk).exists()
        )
