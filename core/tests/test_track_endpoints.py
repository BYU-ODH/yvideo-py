import json
import time

from django.test import TestCase
from django.urls import reverse

from core.factories import AnnotationSetFactory
from core.factories import TrackFactory
from core.factories import UserFactory
from core.models import Track


class TrackEndpointTests(TestCase):
    def setUp(self):
        self.owner = UserFactory(instructor=True)
        self.annotation_set = AnnotationSetFactory(owner=self.owner)
        self.tracks = [
            TrackFactory(
                annotation_set=self.annotation_set, name=name, stack_position=i
            )
            for i, name in enumerate(["Top", "Middle", "Bottom"])
        ]
        self.client.force_login(self.owner)
        session = self.client.session
        # Without it, mozilla_django_oidc's SessionRefresh redirects the request.
        session["oidc_id_token_expiration"] = time.time() + 3600
        session.save()

    def positions(self):
        return dict(
            Track.objects.filter(annotation_set=self.annotation_set).values_list(
                "name", "stack_position"
            )
        )

    def delete(self, track):
        return self.client.delete(reverse("delete_track", args=[track.pk]))

    def test_reorder_accepts_string_ids(self):
        """The editor sends ids read from data attributes, which are strings."""
        top, middle, bottom = self.tracks

        response = self.client.post(
            reverse("update_tracks_stack_positions", args=[self.annotation_set.pk]),
            data=json.dumps(
                {"track_ids": [str(middle.pk), str(top.pk), str(bottom.pk)]}
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.positions(), {"Middle": 0, "Top": 1, "Bottom": 2})

    def test_reorder_rejects_ids_that_are_not_every_track_once(self):
        top, middle, bottom = self.tracks
        for track_ids in [
            ["abc"],
            str(top.pk),
            [top.pk, middle.pk],
            [top.pk, top.pk, bottom.pk],
        ]:
            with self.subTest(track_ids=track_ids):
                response = self.client.post(
                    reverse(
                        "update_tracks_stack_positions", args=[self.annotation_set.pk]
                    ),
                    data=json.dumps({"track_ids": track_ids}),
                    content_type="application/json",
                )

                self.assertEqual(response.status_code, 400)
                self.assertEqual(self.positions(), {"Top": 0, "Middle": 1, "Bottom": 2})

    def test_deleting_the_top_track_renumbers_the_rest(self):
        response = self.delete(self.tracks[0])

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.positions(), {"Middle": 0, "Bottom": 1})
        self.assertEqual(len(response.json()["tracks_html"]), 2)

    def test_deleting_a_middle_track_closes_the_gap(self):
        self.delete(self.tracks[1])

        self.assertEqual(self.positions(), {"Top": 0, "Bottom": 1})

    def test_the_only_track_cannot_be_deleted(self):
        self.delete(self.tracks[1])
        self.delete(self.tracks[2])

        response = self.delete(self.tracks[0])

        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.positions(), {"Top": 0})

    def test_delete_is_offered_unless_the_track_is_alone(self):
        response = self.delete(self.tracks[2])
        self.assertEqual(
            sum("track-menu-delete" in html for html in response.json()["tracks_html"]),
            2,
        )

        response = self.delete(self.tracks[1])
        self.assertNotIn("track-menu-delete", response.json()["tracks_html"][0])

    def rename(self, track, body):
        return self.client.post(
            reverse("update_track_name", args=[track.pk]),
            data=body if isinstance(body, str) else json.dumps(body),
            content_type="application/json",
        )

    def test_rename_saves_the_stripped_name(self):
        track = self.tracks[0]

        response = self.rename(track, {"new_value": "  Intro  "})

        self.assertEqual(response.status_code, 200)
        track.refresh_from_db()
        self.assertEqual(track.name, "Intro")
        # Like the other track endpoints, it answers with every row as now saved.
        tracks_html = response.json()["tracks_html"]
        self.assertEqual(len(tracks_html), 3)
        self.assertIn('<p class="track-name">Intro</p>', tracks_html[0])

    def test_rename_rejects_bad_input(self):
        track = self.tracks[0]
        for body in [
            "not json",
            ["new_value"],
            {},
            {"new_value": ""},
            {"new_value": "   "},
            {"new_value": None},
            {"new_value": 5},
            {"new_value": "x" * 51},
        ]:
            with self.subTest(body=body):
                response = self.rename(track, body)

                self.assertEqual(response.status_code, 400)
                track.refresh_from_db()
                self.assertEqual(track.name, "Top")
