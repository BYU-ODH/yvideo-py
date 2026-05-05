# Create your tests here.
import copy
from functools import cmp_to_key
import json
import unittest

from django.test import TestCase

# from django.urls import reverse
from core.utils import VTTCue
from core.utils import build_cues_from_vtt_file_string
from core.utils import build_vtt_file_string_from_cues
from core.utils import nudge_cue_times
from core.utils import seconds2hms

from . import api
from .factories import AnnotationSetFactory
from .factories import BlankAnnotationFactory
from .factories import CollectionFactory
from .factories import CommentAnnotationFactory
from .factories import ContentFactory
from .factories import MuteAnnotationFactory
from .factories import ResourceFactory
from .factories import ResourceFileFactory
from .factories import TrackFactory
from .factories import UserFactory
from .models import AnnotationSet
from .models import BlurAnnotation
from .models import BlurAnnotationPosition
from .models import PauseAnnotation
from .models import SkipAnnotation


class ApiTests(TestCase):
    @unittest.expectedFailure
    def test_build_auth_header(self):
        new_api = api.Api()
        pattern = r"Bearer[ ]\S*"
        result = new_api.build_auth_header()
        self.assertRegex(result, pattern)

    @unittest.expectedFailure
    def test_get_current_year_term(self):
        pattern = r"[0-9]{4}[1-6]"
        new_api = api.Api()
        result = new_api.get_current_year_term()
        self.assertRegex(result["yearterm"], pattern)

    @unittest.expectedFailure
    def test_calculate_next_year_term(self):
        new_api = api.Api()
        fall_to_winter = new_api.calculate_next_year_term("20255")
        self.assertEqual(fall_to_winter, "20261")
        winter_to_spring = new_api.calculate_next_year_term("20261")
        self.assertEqual(winter_to_spring, "20263")
        spring_to_summer = new_api.calculate_next_year_term("20263")
        self.assertEqual(spring_to_summer, "20264")
        summer_to_fall = new_api.calculate_next_year_term("20264")
        self.assertEqual(summer_to_fall, "20265")


class Seconds2HMSTests(TestCase):
    def test_zero_seconds(self):
        """Test conversion when input is 0 seconds."""
        self.assertEqual(seconds2hms(0), "0:00:00.00")

    def test_fractional_seconds(self):
        """Test conversion with fractional seconds."""
        self.assertEqual(seconds2hms(59.999), "0:01:00.00")  # rounds to 2 decimals

    def test_multiple_hours(self):
        """Test conversion for more than 1 hour."""
        self.assertEqual(seconds2hms(3723.5), "1:02:03.50")  # 1h 2m 3.5s

    def test_minute_carry(self):
        """Test that minutes carry correctly into hours."""
        self.assertEqual(seconds2hms(7200), "2:00:00.00")  # 1h + 60m → 2h 0m

    def test_seconds_carry(self):
        """Test that seconds carry correctly into minutes."""
        self.assertEqual(seconds2hms(3660), "1:01:00.00")

    def test_exact_hour_and_minutes(self):
        """Test exact hour and minute boundary."""
        self.assertEqual(seconds2hms(3720), "1:02:00.00")  # 1h 2m 0s

    def test_negative_seconds_raises(self):
        """Test that negative input raises ValueError."""
        with self.assertRaises(ValueError):
            seconds2hms(-1)


class SubtitlesTests(TestCase):
    def setUp(self):
        self.TEST_VTT_DATA = [
            {
                "type": "REGION",
                "payload": "id:fred\nwidth:40%\nlines:3\nregionanchor:0%,100%\nviewportanchor:10%,90%\nscroll:up",
                "identifier": None,
                "start_time": None,
                "end_time": None,
                "cue_settings": None,
            },
            {
                "type": "STYLE",
                "payload": "::cue {\nbackground-image: linear-gradient(to bottom, dimgray, lightgray);\ncolor: papayawhip;\n}",
                "identifier": None,
                "start_time": None,
                "end_time": None,
                "cue_settings": None,
            },
            {
                "type": "CUE",
                "identifier": None,
                "payload": "Hildy!",
                "start_time": "0:00:00.00",
                "end_time": "0:00:00.90",
                "cue_settings": None,
            },
            {
                "type": "CUE",
                "identifier": None,
                "payload": "How are you?",
                "start_time": "0:00:01.00",
                "end_time": "0:00:01.40",
                "cue_settings": None,
            },
            {
                "type": "NOTE",
                "payload": "This is an example of a note",
                "identifier": None,
                "start_time": None,
                "end_time": None,
                "cue_settings": None,
            },
            {
                "type": "CUE",
                "identifier": None,
                "payload": "Tell me, is the lord of the universe in?",
                "start_time": "0:00:01.50",
                "end_time": "0:00:02.90",
                "cue_settings": None,
            },
            {
                "type": "CUE",
                "identifier": None,
                "payload": "Yes, he's in - in a bad humor",
                "start_time": "0:00:03.00",
                "end_time": "0:00:04.20",
                "cue_settings": None,
            },
            {
                "type": "CUE",
                "identifier": None,
                "payload": "Somebody must've stolen the crown jewels",
                "start_time": "0:00:04.30",
                "end_time": "0:00:06.00",
                "cue_settings": None,
            },
        ]
        self.TEST_VTT = "WEBVTT\n\n"
        cue_index = 0
        num_cues = len(self.TEST_VTT_DATA)
        for vtt_data in self.TEST_VTT_DATA:
            cue_index += 1
            vtt_type = vtt_data["type"]
            if vtt_type == "NOTE" or vtt_type == "REGION" or vtt_type == "STYLE":
                self.TEST_VTT += f"{vtt_type}"
            elif vtt_type == "CUE":
                if vtt_data["identifier"] is not None:
                    self.TEST_VTT += f"{vtt_data['identifier']}\n"
                self.TEST_VTT += f"{vtt_data['start_time']} --> {vtt_data['end_time']}"
                if vtt_data["cue_settings"] is not None:
                    self.TEST_VTT += f" {vtt_data['cue_settings']}"
            self.TEST_VTT += f"\n{vtt_data['payload']}"
            if cue_index < num_cues:
                self.TEST_VTT += "\n\n"

        self.TEST_VTT_CUES = [
            VTTCue(
                type=entry["type"],
                identifier=entry["identifier"],
                payload=entry["payload"],
                start_time=entry["start_time"],
                end_time=entry["end_time"],
                cue_settings=entry["cue_settings"],
            )
            for entry in self.TEST_VTT_DATA
        ]

    def test_build_vtt_file_string_from_cues(self):
        translated_string = build_vtt_file_string_from_cues(self.TEST_VTT_CUES)
        self.assertEqual(translated_string, self.TEST_VTT)

    def test_build_cues_from_vtt_file_string(self):
        cues = build_cues_from_vtt_file_string(self.TEST_VTT)
        new_cues_count = len(cues)
        old_cues_count = len(self.TEST_VTT_CUES)
        self.assertEqual(new_cues_count, old_cues_count)
        for index in range(0, new_cues_count):
            new_cue = cues[index]
            old_cue = self.TEST_VTT_CUES[index]
            self.assertEqual(new_cue.type, old_cue.type)
            self.assertEqual(new_cue.identifier, old_cue.identifier)
            self.assertEqual(new_cue.payload, old_cue.payload)
            self.assertEqual(new_cue.start_time, old_cue.start_time)
            self.assertEqual(new_cue.end_time, old_cue.end_time)
            self.assertEqual(new_cue.cue_settings, old_cue.cue_settings)

    def test_nudge_cue_times(self):
        cues_copy = copy.deepcopy(self.TEST_VTT_CUES)

        forward_nudge = 5
        nudge_cue_times(self.TEST_VTT_CUES, [], forward_nudge)
        for cue_index in range(0, len(cues_copy)):
            cue_copy = cues_copy[cue_index]
            test_cue = self.TEST_VTT_CUES[cue_index]
            if cue_copy.type != "CUE" or test_cue.type != "CUE":
                continue
            self.assertAlmostEqual(
                cue_copy.start_time + forward_nudge, test_cue.start_time
            )
            self.assertAlmostEqual(cue_copy.end_time + forward_nudge, test_cue.end_time)

        backward_nudge = -1 * forward_nudge
        nudge_cue_times(self.TEST_VTT_CUES, [], backward_nudge)
        for cue_index in range(0, len(cues_copy)):
            cue_copy = cues_copy[cue_index]
            test_cue = self.TEST_VTT_CUES[cue_index]
            if cue_copy.type != "CUE" or test_cue.type != "CUE":
                continue
            self.assertAlmostEqual(cue_copy.start_time, test_cue.start_time)
            self.assertAlmostEqual(cue_copy.end_time, test_cue.end_time)

        excluded_cues = [2, 3]
        nudge_cue_times(self.TEST_VTT_CUES, excluded_cues, forward_nudge)
        for cue_index in range(0, len(cues_copy)):
            cue_copy = cues_copy[cue_index]
            test_cue = self.TEST_VTT_CUES[cue_index]
            if cue_copy.type != "CUE" or test_cue.type != "CUE":
                continue
            if cue_index in excluded_cues:
                self.assertAlmostEqual(cue_copy.start_time, test_cue.start_time)
                self.assertAlmostEqual(cue_copy.end_time, test_cue.end_time)
            else:
                self.assertAlmostEqual(
                    cue_copy.start_time + forward_nudge, test_cue.start_time
                )
                self.assertAlmostEqual(
                    cue_copy.end_time + forward_nudge, test_cue.end_time
                )


# class TrackViewTests(TestCase):
#     def setUp(self):
#         self.user = User.objects.create_user(
#             netid="testuser",
#             password="testpass",
#             first_name="Test",
#             last_name="User",
#         )
#         self.resource = Resource.objects.create(
#             name="Test Resource",
#             media_type=Resource.MediaType.VIDEO,
#             requester_netid="testuser",
#         )
#         self.annotation_set = AnnotationSet.objects.create(
#             name="Test Set",
#             resource=self.resource,
#             owner=self.user,
#         )
#         self.tracks = []
#         for i in range(5):
#             self.tracks.append(
#                 Track.objects.create(
#                     annotation_set=self.annotation_set,
#                     name=f"Track {i + 1}",
#                     stack_position=i,
#                 )
#             )
#         self.client.login(username="testuser", password="testpass")

#     # --- update_track ---

#     def test_update_track_name(self):
#         track = self.tracks[1]
#         response = self.client.post(
#             reverse("update_track"),
#             data=json.dumps({"track_id": track.pk, "new_track_name": "Renamed"}),
#             content_type="application/json",
#         )
#         self.assertEqual(response.status_code, 200)
#         track.refresh_from_db()
#         self.assertEqual(track.name, "Renamed")

#     def test_update_track_stack_position(self):
#         track = self.tracks[2]
#         response = self.client.post(
#             reverse("update_track"),
#             data=json.dumps({"track_id": track.pk, "new_stack_position": 10}),
#             content_type="application/json",
#         )
#         self.assertEqual(response.status_code, 200)
#         track.refresh_from_db()
#         self.assertEqual(track.stack_position, 10)

#     def test_update_track_name_and_position_together(self):
#         track = self.tracks[3]
#         response = self.client.post(
#             reverse("update_track"),
#             data=json.dumps(
#                 {
#                     "track_id": track.pk,
#                     "new_track_name": "Updated",
#                     "new_stack_position": 7,
#                 }
#             ),
#             content_type="application/json",
#         )
#         self.assertEqual(response.status_code, 200)
#         track.refresh_from_db()
#         self.assertEqual(track.name, "Updated")
#         self.assertEqual(track.stack_position, 7)

#     def test_update_track_no_fields_changes_nothing(self):
#         track = self.tracks[1]
#         original_name = track.name
#         original_position = track.stack_position
#         response = self.client.post(
#             reverse("update_track"),
#             data=json.dumps({"track_id": track.pk}),
#             content_type="application/json",
#         )
#         self.assertEqual(response.status_code, 200)
#         track.refresh_from_db()
#         self.assertEqual(track.name, original_name)
#         self.assertEqual(track.stack_position, original_position)

#     def test_update_track_invalid_id_returns_404(self):
#         response = self.client.post(
#             reverse("update_track"),
#             data=json.dumps({"track_id": 99999, "new_track_name": "Ghost"}),
#             content_type="application/json",
#         )
#         self.assertEqual(response.status_code, 404)

#     # --- update_track_positions_in_set ---

#     def test_update_track_positions_reverses_order(self):
#         reversed_ids = [t.pk for t in reversed(self.tracks)]
#         response = self.client.post(
#             reverse("update_tracks_stack_positions"),
#             data=json.dumps({"track_ids": reversed_ids}),
#             content_type="application/json",
#         )
#         self.assertEqual(response.status_code, 200)
#         for expected_position, track in enumerate(reversed(self.tracks)):
#             track.refresh_from_db()
#             self.assertEqual(track.stack_position, expected_position)

#     def test_update_track_positions_assigns_sequential_positions(self):
#         ids = [t.pk for t in self.tracks]
#         response = self.client.post(
#             reverse("update_tracks_stack_positions"),
#             data=json.dumps({"track_ids": ids}),
#             content_type="application/json",
#         )
#         self.assertEqual(response.status_code, 200)
#         for expected_position, track in enumerate(self.tracks):
#             track.refresh_from_db()
#             self.assertEqual(track.stack_position, expected_position)

#     def test_update_track_positions_returns_tracks_html(self):
#         ids = [t.pk for t in self.tracks]
#         response = self.client.post(
#             reverse("update_tracks_stack_positions"),
#             data=json.dumps({"track_ids": ids}),
#             content_type="application/json",
#         )
#         self.assertEqual(response.status_code, 200)
#         data = response.json()
#         self.assertIn("tracks_html", data)
#         self.assertEqual(len(data["tracks_html"]), len(self.tracks))

#     def test_update_track_positions_missing_track_ids_returns_400(self):
#         response = self.client.post(
#             reverse("update_tracks_stack_positions"),
#             data=json.dumps({}),
#             content_type="application/json",
#         )
#         self.assertEqual(response.status_code, 400)

#     def test_update_track_positions_single_track(self):
#         track = self.tracks[2]
#         response = self.client.post(
#             reverse("update_tracks_stack_positions"),
#             data=json.dumps({"track_ids": [track.pk]}),
#             content_type="application/json",
#         )
#         self.assertEqual(response.status_code, 200)
#         track.refresh_from_db()
#         self.assertEqual(track.stack_position, 0)

#     # --- create_track ---

#     def test_create_track_with_name(self):
#         response = self.client.post(
#             reverse("create_track"),
#             data=json.dumps(
#                 {"annotation_set_id": self.annotation_set.pk, "track_name": "My Track"}
#             ),
#             content_type="application/json",
#         )
#         self.assertEqual(response.status_code, 200)
#         self.assertTrue(
#             Track.objects.filter(
#                 annotation_set=self.annotation_set, name="My Track"
#             ).exists()
#         )

#     def test_create_track_stack_position_is_highest_plus_one(self):
#         highest = self.annotation_set.get_highest_stack_position()
#         response = self.client.post(
#             reverse("create_track"),
#             data=json.dumps({"annotation_set_id": self.annotation_set.pk}),
#             content_type="application/json",
#         )
#         self.assertEqual(response.status_code, 200)
#         new_track = (
#             Track.objects.filter(annotation_set=self.annotation_set)
#             .order_by("-stack_position")
#             .first()
#         )
#         self.assertEqual(new_track.stack_position, highest + 1)

#     def test_create_track_returns_tracks_html(self):
#         response = self.client.post(
#             reverse("create_track"),
#             data=json.dumps({"annotation_set_id": self.annotation_set.pk}),
#             content_type="application/json",
#         )
#         self.assertEqual(response.status_code, 200)
#         data = response.json()
#         self.assertIn("tracks_html", data)
#         self.assertTrue(data["tracks_html"] != "")

#     def test_create_track_missing_annotation_set_id_returns_400(self):
#         response = self.client.post(
#             reverse("create_track"),
#             data=json.dumps({}),
#             content_type="application/json",
#         )
#         self.assertEqual(response.status_code, 400)

#     # --- delete_track ---

#     def test_delete_track_removes_it(self):
#         track = self.tracks[4]
#         response = self.client.delete(
#             reverse("delete_track", kwargs={"track_id": track.pk})
#         )
#         self.assertEqual(response.status_code, 200)
#         self.assertFalse(Track.objects.filter(pk=track.pk).exists())

#     def test_delete_primary_track_returns_400(self):
#         """Track at stack_position 0 must not be deletable."""
#         primary_track = self.tracks[0]
#         response = self.client.delete(
#             reverse("delete_track", kwargs={"track_id": primary_track.pk})
#         )
#         self.assertEqual(response.status_code, 400)
#         self.assertTrue(Track.objects.filter(pk=primary_track.pk).exists())

#     def test_delete_non_primary_track_leaves_others_intact(self):
#         track_to_delete = self.tracks[3]
#         remaining_ids = {t.pk for t in self.tracks if t != track_to_delete}
#         self.client.delete(
#             reverse("delete_track", kwargs={"track_id": track_to_delete.pk})
#         )
#         for pk in remaining_ids:
#             self.assertTrue(Track.objects.filter(pk=pk).exists())

#     def test_delete_nonexistent_track_returns_500(self):
#         response = self.client.delete(
#             reverse("delete_track", kwargs={"track_id": 99999})
#         )
#         self.assertEqual(response.status_code, 400)


# The tests in AnnotationSetCreateForContentTests were created by Claude and reviewed by BDR 4/24/2026
# Except then the tests turned out to suck and I had to refactor them. AI did write the test setup though. BDR 4/30/2026
class AnnotationSetCreateForContentTests(TestCase):
    def setUp(self):
        self.owner = UserFactory(instructor=True)
        self.resource = ResourceFactory()
        self.resource_file = ResourceFileFactory(resource=self.resource)
        self.collection = CollectionFactory(owner=self.owner)
        self.content = ContentFactory(
            collection=self.collection,
            resource_file=self.resource_file,
        )

        self.original_set = AnnotationSetFactory(
            name="Original Set",
            resource=self.resource,
            owner=self.owner,
        )
        self.tracks = [
            TrackFactory(
                annotation_set=self.original_set,
                name=f"Track {i + 1}",
                stack_position=i,
            )
            for i in range(5)
        ]

        # Track 0 holds an example of every annotation subclass so per-track
        # copy/serialization paths are exercised for all types on a single track.
        full_coverage_track = self.tracks[0]

        self.mute_annotation = MuteAnnotationFactory(
            track=full_coverage_track,
            name="Mute 1",
            start_time=0.0,
            end_time=2.0,
            description="mute opening",
        )
        self.comment_annotation = CommentAnnotationFactory(
            track=full_coverage_track,
            name="Comment 1",
            start_time=3.0,
            end_time=5.0,
            description="opening comment",
            text="Observe the framing",
            x=20.0,
            y=30.0,
        )
        self.blank_annotation = BlankAnnotationFactory(
            track=full_coverage_track,
            name="Blank 1",
            start_time=6.0,
            end_time=8.0,
            description="blank interlude",
            type="k",
        )
        self.skip_annotation = SkipAnnotation.objects.create(
            track=full_coverage_track,
            name="Skip 1",
            start_time=9.0,
            end_time=12.0,
            description="skip intro",
            message="Skipping introduction",
        )
        self.pause_annotation = PauseAnnotation.objects.create(
            track=full_coverage_track,
            name="Pause 1",
            start_time=13.0,
            end_time=13.0,
            description="pause for prompt",
            message="Discuss what you saw",
        )
        self.blur_annotation = BlurAnnotation.objects.create(
            track=full_coverage_track,
            name="Blur 1",
            start_time=15.0,
            end_time=20.0,
            description="blur face on screen",
        )
        for time, x, y in [(15.0, 10.0, 20.0), (17.5, 15.0, 25.0), (20.0, 20.0, 30.0)]:
            BlurAnnotationPosition.objects.create(
                blur_annotation=self.blur_annotation,
                time=time,
                x=x,
                y=y,
                width=100.0,
                height=80.0,
                blur_amount=60,
                type="blur",
            )

        # Tracks 1-4 collectively cover every annotation type at least once so
        # distributed-across-tracks copy/serialization paths are also exercised.
        MuteAnnotationFactory(
            track=self.tracks[1],
            name="Mute 2",
            start_time=22.0,
            end_time=26.0,
            description="mute second section",
        )
        BlankAnnotationFactory(
            track=self.tracks[2],
            name="Blank 2",
            start_time=27.0,
            end_time=30.0,
            description="blank second section",
            type="#",
        )
        SkipAnnotation.objects.create(
            track=self.tracks[2],
            name="Skip 2",
            start_time=31.0,
            end_time=34.0,
            description="skip interlude",
            message="Skipping interlude",
        )
        PauseAnnotation.objects.create(
            track=self.tracks[3],
            name="Pause 2",
            start_time=35.0,
            end_time=35.0,
            description="pause for reflection",
            message="Reflect before continuing",
        )
        CommentAnnotationFactory(
            track=self.tracks[3],
            name="Comment 2",
            start_time=36.0,
            end_time=40.0,
            description="closing comment",
            text="Note the composition",
            x=40.0,
            y=60.0,
        )
        distributed_blur = BlurAnnotation.objects.create(
            track=self.tracks[4],
            name="Blur 2",
            start_time=42.0,
            end_time=48.0,
            description="blur logo",
        )
        for time, x, y in [(42.0, 5.0, 10.0), (45.0, 6.0, 11.0), (48.0, 7.0, 12.0)]:
            BlurAnnotationPosition.objects.create(
                blur_annotation=distributed_blur,
                time=time,
                x=x,
                y=y,
                width=50.0,
                height=50.0,
                blur_amount=55,
                type="blur",
            )

    def _assert_annotation_set_json_is_correct(self, original_set_json, new_set_json):
        self.assertTrue("tracks" in original_set_json and "tracks" in new_set_json)
        self.assertTrue(len(original_set_json["tracks"]) == len(new_set_json["tracks"]))

        # check that the tracks are equivalent except for id values, indicating successful copy
        for i in range(0, len(original_set_json["tracks"])):
            orig_track = original_set_json["tracks"][i]
            new_track = new_set_json["tracks"][i]
            self.assertTrue(orig_track["name"] == new_track["name"])
            self.assertTrue(orig_track["stack_position"] == new_track["stack_position"])
            self.assertTrue(orig_track["id"] != new_track["id"])

        # check that the annotations are equivalent except for ids and each maps to a real track
        self.assertTrue(
            "annotations" in original_set_json and "annotations" in new_set_json
        )
        self.assertTrue(
            len(original_set_json["annotations"]) == len(new_set_json["annotations"])
        )

        def sort_annotations(a, b):
            a_name = a["name"]
            b_name = b["name"]
            if a_name < b_name:
                return -1
            elif a_name > b_name:
                return 1
            else:
                return 0

        orig_annotations = original_set_json["annotations"]
        orig_annotations.sort(key=cmp_to_key(sort_annotations))
        new_annotations = new_set_json["annotations"]
        new_annotations.sort(key=cmp_to_key(sort_annotations))
        for i in range(0, len(orig_annotations)):
            # check inherited BaseAnnotation attributes
            orig_annotation = orig_annotations[i]
            new_annotation = new_annotations[i]
            self.assertTrue(orig_annotation["name"] == new_annotation["name"])
            self.assertTrue(
                orig_annotation["track_name"] == new_annotation["track_name"]
            )
            self.assertTrue(orig_annotation["start"] == new_annotation["start"])
            self.assertTrue(orig_annotation["end"] == new_annotation["end"])
            self.assertTrue(
                orig_annotation["description"] == new_annotation["description"]
            )
            self.assertTrue(new_annotation["active"])
            orig_type = orig_annotation["type"]
            self.assertTrue(orig_type == new_annotation["type"])

            # check for annotation type specific attributes
            if orig_type == "skip" or orig_type == "pause":
                self.assertTrue(orig_annotation["message"] == new_annotation["message"])
            elif orig_type == "blank":
                self.assertTrue(orig_annotation["type"] == new_annotation["type"])
            elif orig_type == "comment":
                self.assertTrue(orig_annotation["text"] == new_annotation["text"])
                self.assertTrue(orig_annotation["x"] == new_annotation["x"])
                self.assertTrue(orig_annotation["y"] == new_annotation["y"])
            elif orig_type == "censor":
                self.assertTrue(
                    len(orig_annotation["positions"])
                    == len(new_annotation["positions"])
                )
                # check that the positions are correct
                for j in range(0, len(orig_annotation["positions"])):
                    orig_position = orig_annotation["positions"][j]
                    new_position = new_annotation["positions"][j]
                    self.assertTrue(orig_position["id"] != new_position["id"])
                    self.assertTrue(orig_position["time"] == new_position["time"])
                    self.assertTrue(orig_position["x"] == new_position["x"])
                    self.assertTrue(orig_position["y"] == new_position["y"])
                    self.assertTrue(orig_position["width"] == new_position["width"])
                    self.assertTrue(orig_position["height"] == new_position["height"])
                    self.assertTrue(
                        orig_position["blur_amount"] == new_position["blur_amount"]
                    )
                    self.assertTrue(orig_position["type"] == new_position["type"])

    def test_create_for_content_with_annotations_json(self):
        original_set_json = self.original_set.to_player_json()
        annotation_set_json = json.dumps(original_set_json)

        new_set = AnnotationSet.create_for_content(
            content=self.content,
            user=self.owner,
            set_name="Copied From JSON",
            annotation_set_json=annotation_set_json,
        )

        self.assertIsNotNone(new_set)

        # compare to_player_json output of both sets
        new_set_json = new_set.to_player_json()
        self._assert_annotation_set_json_is_correct(original_set_json, new_set_json)

    def test_create_for_content_with_annotation_set_id_to_copy(self):
        original_json = self.original_set.to_player_json()

        new_set = AnnotationSet.create_for_content(
            content=self.content,
            user=self.owner,
            set_name="Copied From Existing Set",
            annotation_set_id_to_copy=self.original_set.pk,
        )

        self.assertIsNotNone(new_set)
        self.assertEqual(new_set.resource, self.resource)
        self.assertEqual(new_set.owner, self.owner)

        self._assert_annotation_set_json_is_correct(
            original_json, new_set.to_player_json()
        )
