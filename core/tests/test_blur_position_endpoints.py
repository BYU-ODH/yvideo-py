"""Authorization and input-handling tests for the blur-position endpoints.

These endpoints previously had no authorization at all - unlike every neighbouring
annotation view - so any authenticated user could create, move, or delete blur positions on
any BlurAnnotation by guessing a numeric id. `delete_blur_position` additionally carried no
method decorator (so it answered GET) and dereferenced a possibly-unbound local on a failed
lookup, turning a bad id into a 500. Blurs cover copyrighted and explicit content, so these
are the tests that keep that hole closed.

The create/update split they were written against is now a single upsert, because the editor
cannot tell the two apart: a drag means "the blur belongs here at the time I am looking at", and
whether that is a new point is a fact about stored data. The upsert semantics themselves are
covered below, since getting them wrong retimes a point the user did not touch.
"""

import json

from django.test import TestCase
from django.urls import reverse

from core.factories import AnnotationSetFactory
from core.factories import BlurAnnotationFactory
from core.factories import BlurAnnotationPositionFactory
from core.factories import TrackFactory
from core.factories import UserFactory
from core.models import BlurAnnotationPosition


class BlurPositionEndpointTests(TestCase):
    def setUp(self):
        self.owner = UserFactory(instructor=True)
        self.stranger = UserFactory(instructor=True)
        self.annotation_set = AnnotationSetFactory(owner=self.owner)
        self.track = TrackFactory(annotation_set=self.annotation_set)
        self.blur = BlurAnnotationFactory(
            track=self.track, start_time=5.0, end_time=12.0
        )
        self.position = BlurAnnotationPositionFactory(
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
            reverse("upsert_blur_position", args=[self.blur.pk]),
            data=json.dumps(payload),
            content_type="application/json",
        )

    # --- authorization -------------------------------------------------------

    def test_upsert_requires_edit_permission(self):
        self.client.force_login(self.stranger)
        response = self._upsert()
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.blur.positions.count(), 1)

    def test_upsert_of_a_named_position_requires_edit_permission(self):
        self.client.force_login(self.stranger)
        response = self._upsert(position_id=self.position.pk)
        self.assertEqual(response.status_code, 403)
        self.position.refresh_from_db()
        self.assertEqual(self.position.x, 12.5)

    def test_delete_requires_edit_permission(self):
        deletable = BlurAnnotationPositionFactory(blur_annotation=self.blur, time=8.0)
        self.client.force_login(self.stranger)
        response = self.client.delete(
            reverse("delete_blur_position", args=[deletable.pk])
        )
        self.assertEqual(response.status_code, 403)
        self.assertTrue(BlurAnnotationPosition.objects.filter(pk=deletable.pk).exists())

    def test_an_editor_on_the_set_may_edit(self):
        self.annotation_set.editors.add(self.stranger)
        self.client.force_login(self.stranger)
        self.assertEqual(self._upsert().status_code, 200)

    def test_a_position_id_from_another_blur_is_not_a_way_in(self):
        """The id is scoped to the annotation in the URL, which is the one authorized above."""
        other_blur = BlurAnnotationFactory(
            track=TrackFactory(annotation_set=AnnotationSetFactory()),
            start_time=0.0,
            end_time=4.0,
        )
        foreign = BlurAnnotationPositionFactory(blur_annotation=other_blur, time=1.0)
        self.client.force_login(self.owner)
        response = self._upsert(position_id=foreign.pk)
        self.assertEqual(response.status_code, 404)
        foreign.refresh_from_db()
        self.assertNotEqual(foreign.x, 10.0)

    # --- upsert semantics ----------------------------------------------------

    def test_a_write_at_a_new_time_adds_a_point(self):
        """The headline fix: a drag at a time with no point must not retime an existing one."""
        self.client.force_login(self.owner)
        self.assertEqual(self._upsert(time=9.0).status_code, 200)

        self.assertEqual([p.time for p in self.blur.positions.all()], [5.0, 9.0])
        self.position.refresh_from_db()
        self.assertEqual(self.position.time, 5.0, "the existing point was retimed")

    def test_a_write_near_an_existing_point_moves_that_point_without_retiming_it(self):
        """Within the snap window the user is editing that point, not making a new one."""
        BlurAnnotationPositionFactory(blur_annotation=self.blur, time=9.02, x=1.0)
        self.client.force_login(self.owner)
        self.assertEqual(self._upsert(time=9.0).status_code, 200)

        self.assertEqual([p.time for p in self.blur.positions.all()], [5.0, 9.02])
        self.assertEqual(self.blur.positions.get(time=9.02).x, 10.0)

    def test_a_named_position_can_be_retimed(self):
        """What the panel's time input and dragging a timeline dot need."""
        moving = BlurAnnotationPositionFactory(blur_annotation=self.blur, time=9.0)
        self.client.force_login(self.owner)
        self.assertEqual(
            self._upsert(position_id=moving.pk, time=11.0).status_code, 200
        )
        self.assertEqual([p.time for p in self.blur.positions.all()], [5.0, 11.0])

    def test_retiming_onto_another_point_is_a_conflict_not_a_500(self):
        occupied = BlurAnnotationPositionFactory(blur_annotation=self.blur, time=9.0)
        BlurAnnotationPositionFactory(blur_annotation=self.blur, time=11.0)
        self.client.force_login(self.owner)
        response = self._upsert(position_id=occupied.pk, time=11.0)
        self.assertEqual(response.status_code, 409)
        # The rejected save must not have poisoned the transaction, and nothing may be lost.
        self.assertEqual([p.time for p in self.blur.positions.all()], [5.0, 9.0, 11.0])

    def test_a_time_outside_the_blurs_window_is_clamped_not_rejected(self):
        """Rounding at a boundary is not worth failing an edit over."""
        self.client.force_login(self.owner)
        self.assertEqual(self._upsert(time=99.0).status_code, 200)
        self.assertEqual([p.time for p in self.blur.positions.all()], [5.0, 12.0])

    def test_the_first_point_stays_pinned_to_the_blurs_start(self):
        """Invariant I5, re-asserted after every write rather than trusted to callers."""
        self.client.force_login(self.owner)
        self._upsert(time=8.0)
        self._upsert(position_id=self.position.pk, time=10.0)
        self.assertEqual(self.blur.positions.first().time, self.blur.start_time)

    def test_the_response_carries_the_positions_the_player_needs(self):
        self.client.force_login(self.owner)
        payload = self._upsert(time=9.0).json()
        self.assertEqual(
            [position["time"] for position in payload["positions"]], [5.0, 9.0]
        )
        # The panel and the timeline bar are both rebuilt from the response, so the editor never
        # has to reconstruct row numbering or dot placement itself.
        self.assertIn("blurPositions", payload)
        self.assertIn("trackItem", payload)

    # --- input handling ------------------------------------------------------

    def test_zero_x_and_y_are_accepted(self):
        """A box flush against the left or top edge is legitimate, not a bad request.

        These were previously rejected by a falsy check that could not distinguish 0 from
        a missing value.
        """
        self.client.force_login(self.owner)
        response = self._upsert(x=0, y=0)
        self.assertEqual(response.status_code, 200)
        created = self.blur.positions.get(time=7.0)
        self.assertEqual((created.x, created.y), (0.0, 0.0))

    def test_missing_field_is_still_a_bad_request(self):
        self.client.force_login(self.owner)
        self.assertEqual(self._upsert(x=None).status_code, 400)
        self.assertEqual(self._upsert(width="wide").status_code, 400)

    def test_upsert_with_an_unknown_annotation_is_404(self):
        self.client.force_login(self.owner)
        response = self.client.post(
            reverse("upsert_blur_position", args=[self.blur.pk + 10_000]),
            data=json.dumps({"time": 7.0, "x": 1, "y": 2, "width": 3, "height": 4}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 404)

    # --- delete --------------------------------------------------------------

    def test_delete_removes_the_point_and_returns_the_rebuilt_panel(self):
        deletable = BlurAnnotationPositionFactory(blur_annotation=self.blur, time=8.0)
        self.client.force_login(self.owner)
        response = self.client.delete(
            reverse("delete_blur_position", args=[deletable.pk])
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual([p.time for p in self.blur.positions.all()], [5.0])
        self.assertEqual([p["time"] for p in response.json()["positions"]], [5.0])

    def test_the_first_point_cannot_be_deleted(self):
        """A blur with no positions has no geometry at all and silently stops rendering."""
        self.client.force_login(self.owner)
        response = self.client.delete(
            reverse("delete_blur_position", args=[self.position.pk])
        )
        self.assertEqual(response.status_code, 409)
        self.assertTrue(
            BlurAnnotationPosition.objects.filter(pk=self.position.pk).exists()
        )

    def test_delete_with_unknown_id_is_404_not_500(self):
        self.client.force_login(self.owner)
        response = self.client.delete(
            reverse("delete_blur_position", args=[self.position.pk + 10_000])
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
            reverse("delete_blur_position", args=[self.position.pk])
        )
        self.assertEqual(response.status_code, 405)
        self.assertTrue(
            BlurAnnotationPosition.objects.filter(pk=self.position.pk).exists()
        )
