"""Authorization and input-handling tests for the blur-position endpoints.

These endpoints previously had no authorization at all - unlike every neighbouring
annotation view - so any authenticated user could create, move, or delete blur positions on
any BlurAnnotation by guessing a numeric id. `delete_blur_position` additionally carried no
method decorator (so it answered GET) and dereferenced a possibly-unbound local on a failed
lookup, turning a bad id into a 500. Blurs cover copyrighted and explicit content, so these
are the tests that keep that hole closed.
"""

import json

from django.test import RequestFactory
from django.test import TestCase
from django.urls import reverse

from core.factories import AnnotationSetFactory
from core.factories import BlurAnnotationFactory
from core.factories import BlurAnnotationPositionFactory
from core.factories import TrackFactory
from core.factories import UserFactory
from core.models import BlurAnnotationPosition
from core.views_video_editor import delete_blur_position


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

    def _create(self, **overrides):
        payload = {
            "parent_annotation_id": self.blur.pk,
            "time": 7.0,
            "x": 10.0,
            "y": 20.0,
            "width": 30.0,
            "height": 40.0,
        }
        payload.update(overrides)
        return self.client.post(
            reverse("create_blur_position"),
            data=json.dumps(payload),
            content_type="application/json",
        )

    def _update(self, **overrides):
        payload = {
            "position_id": self.position.pk,
            "time": 6.0,
            "x": 1.0,
            "y": 2.0,
            "width": 3.0,
            "height": 4.0,
        }
        payload.update(overrides)
        return self.client.post(
            reverse("update_blur_position"),
            data=json.dumps(payload),
            content_type="application/json",
        )

    # --- authorization -------------------------------------------------------

    def test_create_requires_edit_permission(self):
        self.client.force_login(self.stranger)
        response = self._create()
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.blur.positions.count(), 1)

    def test_update_requires_edit_permission(self):
        self.client.force_login(self.stranger)
        response = self._update()
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
        self.assertEqual(self._create().status_code, 200)

    # --- input handling ------------------------------------------------------

    def test_zero_x_and_y_are_accepted(self):
        """A box flush against the left or top edge is legitimate, not a bad request.

        These were previously rejected by a falsy check that could not distinguish 0 from
        a missing value.
        """
        self.client.force_login(self.owner)
        response = self._create(x=0, y=0)
        self.assertEqual(response.status_code, 200)
        created = self.blur.positions.get(time=7.0)
        self.assertEqual((created.x, created.y), (0.0, 0.0))

    def test_missing_field_is_still_a_bad_request(self):
        self.client.force_login(self.owner)
        self.assertEqual(self._create(x=None).status_code, 400)

    def test_delete_with_unknown_id_is_404_not_500(self):
        self.client.force_login(self.owner)
        response = self.client.delete(
            reverse("delete_blur_position", args=[self.position.pk + 10_000])
        )
        self.assertEqual(response.status_code, 404)

    def test_delete_rejects_get(self):
        """The view is DELETE-only; it used to carry no method decorator at all.

        Called through the view function rather than self.client because
        mozilla_django_oidc's SessionRefresh middleware intercepts GET (and only GET)
        requests to bounce them to the SSO endpoint, so a client-level GET never reaches
        the view to have its method checked.
        """
        request = RequestFactory().get(
            reverse("delete_blur_position", args=[self.position.pk])
        )
        request.user = self.owner
        response = delete_blur_position(request, self.position.pk)
        self.assertEqual(response.status_code, 405)
        self.assertTrue(
            BlurAnnotationPosition.objects.filter(pk=self.position.pk).exists()
        )

    def test_update_with_unknown_id_is_404_not_500(self):
        self.client.force_login(self.owner)
        self.assertEqual(
            self._update(position_id=self.position.pk + 10_000).status_code, 404
        )
