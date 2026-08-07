"""End-to-end coverage for issue #322 items 2 and 3, through the real update endpoint.

BlurAnnotation.reconcile_positions is unit-tested in test_blur_positions.py; what these add is
the integration point. update_annotation has to capture start_time/end_time *before* it
reassigns them, or reconcile cannot tell a move from a resize -- and that mistake would leave
the model method correct while the feature was broken.
"""

import json

from django.test import TestCase
from django.urls import reverse

from core.factories import AnnotationSetFactory
from core.factories import BlurAnnotationFactory
from core.factories import BlurAnnotationPositionFactory
from core.factories import ContentFactory
from core.factories import PlaylistFactory
from core.factories import ResourceFileFactory
from core.factories import TrackFactory
from core.factories import UserFactory


class BlurReconcileThroughEndpointTests(TestCase):
    def setUp(self):
        self.owner = UserFactory(instructor=True)
        self.client.force_login(self.owner)
        self.resource_file = ResourceFileFactory()
        self.annotation_set = AnnotationSetFactory(
            resource=self.resource_file.resource, owner=self.owner
        )
        self.content = ContentFactory(
            playlist=PlaylistFactory(owner=self.owner),
            resource_file=self.resource_file,
            annotation_set=self.annotation_set,
        )
        self.track = TrackFactory(annotation_set=self.annotation_set)
        self.blur = BlurAnnotationFactory(
            track=self.track, start_time=10.0, end_time=15.0
        )
        self.blur.positions.all().delete()
        for time, x in ((10.0, 10.0), (12.0, 20.0), (15.0, 30.0)):
            BlurAnnotationPositionFactory(
                blur_annotation=self.blur, time=time, x=x, y=5.0
            )

    def _update(self, start_time, end_time):
        response = self.client.post(
            reverse("update_annotation", args=["blur", self.blur.pk]),
            data=json.dumps(
                {
                    "content_id": self.content.pk,
                    "start_time": start_time,
                    "end_time": end_time,
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200, response.content[:300])
        # A save writes a new version (BaseAnnotation.edit) and reconciles *its* copy of the
        # positions; the version posted to keeps the times undo would restore. Follow the
        # response to the active version rather than refreshing the superseded one.
        self.blur = self.blur.__class__.objects.get(pk=response.json()["annotation_id"])
        return [p.time for p in self.blur.positions.all()]

    # The one outcome that *discriminates* a resize from a move, which is the wiring this module is
    # about: capture the old window before reassigning, or the model is told the wrong thing and does
    # the wrong thing correctly. ReconcilePositionsTests covers the rest against the model directly.
    def test_dragging_the_left_handle_moves_the_first_position(self):
        """#322 item 2: the *start* time updates, and later positions are left alone."""
        self.assertEqual(self._update(13.0, 15.0), [13.0, 15.0])
        # The tween at t=13 between the positions at 12 (x=20) and 15 (x=30) - what was on screen
        # there before the resize - rather than either stored position's geometry.
        self.assertAlmostEqual(
            self.blur.positions.first().x,
            23.333,
            2,
            "the first position should carry the geometry that was showing at the new start",
        )

    def test_creating_a_blur_seeds_one_position_at_its_start_time(self):
        response = self.client.post(
            reverse("create_annotation", args=["blur", self.track.pk]),
            data=json.dumps(
                {
                    "content_id": self.content.pk,
                    "start_time": 4.0,
                    "end_time": 9.0,
                    "description": "seeded blur",
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200, response.content[:300])

        created = (
            self.blur.__class__.objects.filter(track=self.track)
            .exclude(pk=self.blur.pk)
            .first()
        )
        self.assertIsNotNone(created)
        positions = list(created.positions.all())
        self.assertEqual(len(positions), 1)
        self.assertEqual(
            positions[0].time,
            created.start_time,
            "the seeded position must sit at the blur's start, not at time 0",
        )
