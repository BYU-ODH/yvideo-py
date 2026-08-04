# Create your tests here.
import copy
from datetime import date
from functools import cmp_to_key
import json
import re
import unittest

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.test import override_settings
from django.urls import reverse

from .. import api
from ..factories import AnnotationSetFactory
from ..factories import BlankAnnotationFactory
from ..factories import ClipFactory
from ..factories import CommentAnnotationFactory
from ..factories import ContentFactory
from ..factories import CourseFactory
from ..factories import MuteAnnotationFactory
from ..factories import PlaylistFactory
from ..factories import ResourceFactory
from ..factories import ResourceFileFactory
from ..factories import SubtitleFactory
from ..factories import TrackFactory
from ..factories import UserCourseFactory
from ..factories import UserFactory
from ..models import AnnotationSet
from ..models import BlurAnnotation
from ..models import BlurAnnotationPosition
from ..models import Content
from ..models import PauseAnnotation
from ..models import Resource
from ..models import ResourceFileKey
from ..models import SkipAnnotation
from ..models import validate_font_color
from ..utils import VTTCue
from ..utils import build_cues_from_vtt_file_string
from ..utils import build_vtt_file_string_from_cues
from ..utils import estimate_current_yearterm
from ..utils import nudge_cue_times
from ..utils import seconds2hms


class ResourceImdbIdGenerationTests(TestCase):
    def test_omitted_imdb_id_is_auto_generated(self):
        resource = Resource.objects.create(
            name="Test Resource", requester_username="req00001"
        )
        self.assertRegex(resource.imdb_id, r"^BYU\d{10}$")

    def test_blank_imdb_id_is_auto_generated(self):
        resource = Resource.objects.create(
            name="Test Resource 2", requester_username="req00002", imdb_id=""
        )
        self.assertRegex(resource.imdb_id, r"^BYU\d{10}$")

    def test_field_allows_null_so_blank_placeholders_never_collide(self):
        # Resource.save() normalizes a missing/blank imdb_id to None before the
        # initial insert (rather than saving ""), so two Resources created
        # without an explicit imdb_id can't collide on the unique constraint
        # before generate_internal_imdb_id assigns each its real, pk-derived
        # value. That requires the field itself to allow NULL.
        self.assertTrue(Resource._meta.get_field("imdb_id").null)


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


class EstimateCurrentYeartermTests(TestCase):
    """Boundaries approximate the BYU academic calendar; see utils.py."""

    def test_term_boundaries(self):
        cases = [
            (date(2026, 1, 1), "20261"),  # winter break -> Winter
            (date(2026, 2, 14), "20261"),  # mid Winter semester
            (date(2026, 4, 21), "20261"),  # last day counted as Winter
            (date(2026, 4, 22), "20263"),  # first day counted as Spring
            (date(2026, 5, 15), "20263"),  # mid Spring term
            (date(2026, 6, 18), "20263"),  # last day counted as Spring
            (date(2026, 6, 19), "20264"),  # first day counted as Summer
            (date(2026, 7, 17), "20264"),  # mid Summer term
            (date(2026, 8, 21), "20264"),  # last day counted as Summer
            (date(2026, 8, 22), "20265"),  # first day counted as Fall
            (date(2026, 10, 31), "20265"),  # mid Fall semester
            (date(2026, 12, 31), "20265"),  # winter break stays Fall
            (date(2027, 1, 1), "20271"),  # year rolls over at Jan 1
        ]
        for today, expected in cases:
            with self.subTest(today=today):
                self.assertEqual(estimate_current_yearterm(today), expected)

    def test_defaults_to_current_date(self):
        yearterm = estimate_current_yearterm()
        self.assertRegex(yearterm, r"^\d{4}[1345]$")

    def test_course_factory_uses_estimate(self):
        course = CourseFactory()
        self.assertEqual(course.yearterm, estimate_current_yearterm())


class DisplayYeartermTests(TestCase):
    def test_course_valid_yearterm(self):
        course = CourseFactory(yearterm="20261")
        self.assertEqual(course.display_yearterm(), "Winter 2026")
        self.assertIn("Winter 2026", str(course))

    def test_course_invalid_term_returns_raw_yearterm(self):
        course = CourseFactory(yearterm="20269")
        self.assertEqual(course.display_yearterm(), "20269")

    def test_course_empty_yearterm_does_not_raise(self):
        course = CourseFactory(yearterm="")
        self.assertEqual(course.display_yearterm(), "")
        str(course)  # must not raise UnboundLocalError

    def test_user_course_invalid_term_returns_raw_yearterm(self):
        user_course = UserCourseFactory(yearterm="20262")
        self.assertEqual(user_course.display_yearterm(), "20262")
        str(user_course)  # must not raise UnboundLocalError


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


class FontColorValidationTests(TestCase):
    def setUp(self):
        self.invalid_hexcodes = [
            "G12B34",
            "Z99999",
            "FF@000",
            "123L56",
            "??FF00",
            "FF00",
            "ABC12",
            "#ABC12",
            "A1",
            "A",
            "CC00FF1",
        ]
        self.valid_hexcodes = [
            "FFFFFF",
            "000000",
            "FF0000",
            "00FF00",
            "0000FF",
            "FFFF00",
            "FFA500",
            "800080",
            "FFC0CB",
            "FFF",
            "000",
            "F00",
            "0F0",
            "00F",
            "CCC",
            "abc123",
            "def456",
            "a1b2c3",
            "d4e5f6",
            "abcdef",
            "f0e1d2",
            "c3b2a1",
            "af0123",
            "be4567",
            "cf8901",
            "ace",
            "bdf",
            "fba",
            "ead",
            "cba",
        ]

    def test_invalid_hexcodes(self):
        for code in self.invalid_hexcodes:
            with self.assertRaises(ValidationError):
                validate_font_color(code)

    def test_valid_hexcodes(self):
        for code in self.valid_hexcodes:
            try:
                validate_font_color(code)
            except Exception:
                self.fail("Failed to validate valid hexcode")


# class TrackViewTests(TestCase):
#     def setUp(self):
#         self.user = User.objects.create_user(
#             username="111220000",
#             netid="testuser"
#             password="testpass",
#             first_name="Test",
#             last_name="User",
#         )
#         self.resource = Resource.objects.create(
#             name="Test Resource",
#             media_type=Resource.MediaType.VIDEO,
#             requester_username="111220000",
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
#         self.client.login(username="111220000", password="testpass")

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
# Except then it I realized the tests were bad and I had to refactor them. AI did write the test setup though. BDR 4/30/2026
class AnnotationSetCreateForContentTests(TestCase):
    def setUp(self):
        self.owner = UserFactory(instructor=True)
        self.resource = ResourceFactory()
        self.resource_file = ResourceFileFactory(resource=self.resource)
        self.playlist = PlaylistFactory(owner=self.owner)
        self.content = ContentFactory(
            playlist=self.playlist,
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
            top_left_x=20.0,
            top_left_y=30.0,
            bottom_right_x=20.0,
            bottom_right_y=30.0,
            font_size_in_rem=1.0,
            font_color="abcdef",
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
            top_left_x=20.0,
            top_left_y=30.0,
            bottom_right_x=20.0,
            bottom_right_y=30.0,
            font_size_in_rem=1.0,
            font_color="abcdef",
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
                self.assertTrue(
                    orig_annotation["top_left_x"] == new_annotation["top_left_x"]
                )
                self.assertTrue(
                    orig_annotation["top_left_y"] == new_annotation["top_left_y"]
                )
                self.assertTrue(
                    orig_annotation["bottom_right_x"]
                    == new_annotation["bottom_right_x"]
                )
                self.assertTrue(
                    orig_annotation["bottom_right_y"]
                    == new_annotation["bottom_right_y"]
                )
                self.assertTrue(
                    orig_annotation["font_size_in_rem"]
                    == new_annotation["font_size_in_rem"]
                )
                self.assertTrue(
                    orig_annotation["font_color"] == new_annotation["font_color"]
                )
            elif orig_type == "blur":
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


class ContentClipsOnlyViewTests(TestCase):
    """Tests for the clips_only field in the get_player_data and update_content views."""

    def setUp(self):
        self.user = UserFactory(instructor=True)
        self.client.force_login(self.user)
        self.playlist = PlaylistFactory(owner=self.user)
        self.resource_file = ResourceFileFactory()
        self.content_clips_only = ContentFactory(
            playlist=self.playlist,
            resource_file=self.resource_file,
            clips_only=True,
        )
        self.content_no_clips_only = ContentFactory(
            playlist=self.playlist,
            resource_file=self.resource_file,
            clips_only=False,
        )

        # Create a ResourceFileKey so the player endpoint can be accessed
        ResourceFileKey.objects.create(
            user=self.user,
            resource_file=self.resource_file,
        )

        # Set up an annotation set with a clip
        self.annotation_set = AnnotationSetFactory(
            resource=self.resource_file.resource,
            owner=self.user,
        )
        track = TrackFactory(annotation_set=self.annotation_set)
        ClipFactory(
            track=track,
            start_time=10.0,
            end_time=30.0,
        )
        self.content_clips_only.annotation_set = self.annotation_set
        self.content_clips_only.save()

    def _get_player_data(self, content):
        return self.client.post(reverse("get_player_data", args=[content.pk]))

    def _update_content(self, content, **field_overrides):
        payload = {
            "id": content.pk,
            "title": content.title,
            "description": content.description,
            "words": content.words,
            "allow_definitions": content.allow_definitions,
            "allow_notes": content.allow_notes,
            "allow_captions": content.allow_captions,
            "allow_fast_playback": content.allow_fast_playback,
            "clips_only": content.clips_only,
            "published": content.published,
        }
        payload.update(field_overrides)
        return self.client.post(
            reverse("update_content"),
            data=json.dumps(payload),
            content_type="application/json",
        )

    def test_player_data_reflects_the_content_clips_only_flag(self):
        clips_only_response = self._get_player_data(self.content_clips_only)
        no_clips_only_response = self._get_player_data(self.content_no_clips_only)

        self.assertTrue(clips_only_response.json()["clipsOnly"])
        self.assertFalse(no_clips_only_response.json()["clipsOnly"])

    def test_player_data_returns_clips_list(self):
        response = self._get_player_data(self.content_clips_only)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("clips", data)
        self.assertEqual(len(data["clips"]), 1)
        clip = data["clips"][0]
        self.assertEqual(clip["class_type"], "Clip")
        self.assertAlmostEqual(clip["start"], 10.0)
        self.assertAlmostEqual(clip["end"], 30.0)

    def test_player_data_does_not_duplicate_clips_in_annotations(self):
        response = self._get_player_data(self.content_clips_only)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        annotation_class_types = [a["class_type"] for a in data["annotations"]]
        self.assertNotIn("Clip", annotation_class_types)

    def test_update_content_toggles_clips_only(self):
        set_response = self._update_content(self.content_no_clips_only, clips_only=True)
        clear_response = self._update_content(self.content_clips_only, clips_only=False)

        self.assertEqual(set_response.status_code, 200)
        self.assertEqual(clear_response.status_code, 200)
        self.content_no_clips_only.refresh_from_db()
        self.content_clips_only.refresh_from_db()
        self.assertTrue(self.content_no_clips_only.clips_only)
        self.assertFalse(self.content_clips_only.clips_only)


@override_settings(
    STORAGES={
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {
            "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"
        },
    }
)
class ContentHasClipsWarningTests(TestCase):
    """Tests for Content.has_clips() and the settings-form no-clips warning."""

    def setUp(self):
        self.user = UserFactory(instructor=True)
        # Rendering the settings form is a GET request, which
        # mozilla_django_oidc's SessionRefresh middleware intercepts unless the
        # session's auth backend is explicitly non-OIDC (see test_legacy_migration.py
        # for the same pattern).
        self.client.force_login(
            self.user, backend="django.contrib.auth.backends.ModelBackend"
        )
        self.playlist = PlaylistFactory(owner=self.user)
        self.resource_file = ResourceFileFactory()
        self.annotation_set = AnnotationSetFactory(
            resource=self.resource_file.resource,
            owner=self.user,
        )
        track = TrackFactory(annotation_set=self.annotation_set)
        ClipFactory(track=track, start_time=5.0, end_time=15.0)

    def test_has_clips_is_true_when_an_active_clip_exists(self):
        content = ContentFactory(
            playlist=self.playlist,
            resource_file=self.resource_file,
            annotation_set=self.annotation_set,
        )
        self.assertTrue(content.has_clips())

    def test_has_clips_is_false_without_an_annotation_set(self):
        content = ContentFactory(
            playlist=self.playlist,
            resource_file=self.resource_file,
        )
        self.assertFalse(content.has_clips())

    def _clips_only_warning_is_visible(self, content):
        # The warning <div> is always rendered (so updateContentSettings.js can
        # toggle it live as the checkbox changes) - whether it's shown or not
        # comes down to the "hidden" attribute on that element, not whether its
        # text appears in the response at all.
        response = self.client.get(
            reverse("render_content_settings_form", args=[content.pk])
        )
        self.assertEqual(response.status_code, 200)
        warning_tag = re.search(
            rb'<div id="clips-only-warning"[^>]*>', response.content
        )
        self.assertIsNotNone(warning_tag, "clips-only-warning element not rendered")
        return b"hidden" not in warning_tag.group()

    def test_settings_form_warns_when_clips_only_is_on_with_no_clips(self):
        content = ContentFactory(
            playlist=self.playlist,
            resource_file=self.resource_file,
            clips_only=True,
        )
        self.assertTrue(self._clips_only_warning_is_visible(content))

    def test_settings_form_does_not_warn_when_clips_exist(self):
        content = ContentFactory(
            playlist=self.playlist,
            resource_file=self.resource_file,
            annotation_set=self.annotation_set,
            clips_only=True,
        )
        self.assertFalse(self._clips_only_warning_is_visible(content))

    def test_settings_form_does_not_warn_when_clips_only_is_off(self):
        content = ContentFactory(
            playlist=self.playlist,
            resource_file=self.resource_file,
            clips_only=False,
        )
        self.assertFalse(self._clips_only_warning_is_visible(content))


class DefaultSubtitleTrackTests(TestCase):
    """Tests for the instructor-chosen default subtitle track on Content."""

    def setUp(self):
        self.owner = UserFactory(instructor=True)
        self.resource = ResourceFactory()
        self.resource_file = ResourceFileFactory(resource=self.resource)
        self.playlist = PlaylistFactory(owner=self.owner)
        self.content = ContentFactory(
            playlist=self.playlist,
            resource_file=self.resource_file,
        )
        self.subtitle_en = SubtitleFactory(resource=self.resource, owner=self.owner)
        self.subtitle_es = SubtitleFactory(resource=self.resource, owner=self.owner)

    def test_default_subtitle_track_is_null_by_default(self):
        content = Content.objects.get(pk=self.content.pk)
        self.assertIsNone(content.default_subtitle_track)

    def test_get_subtitles_default_flag_false_when_no_default_set(self):
        subtitles = self.content.get_subtitles()
        self.assertEqual(len(subtitles), 2)
        for sub in subtitles:
            self.assertFalse(sub["default"])

    def test_get_subtitles_marks_exactly_one_default(self):
        self.content.default_subtitle_track = self.subtitle_en
        self.content.save()
        subtitles = self.content.get_subtitles()
        defaults = [s for s in subtitles if s["default"]]
        non_defaults = [s for s in subtitles if not s["default"]]
        self.assertEqual(len(defaults), 1)
        self.assertEqual(len(non_defaults), 1)
        self.assertEqual(defaults[0]["id"], self.subtitle_en.pk)

    def test_get_subtitles_default_flag_in_player_json(self):
        self.content.default_subtitle_track = self.subtitle_es
        self.content.save()
        player_json = self.content.get_player_json()
        subtitle_tracks = player_json["subtitleTracks"]
        defaults = [s for s in subtitle_tracks if s["default"]]
        self.assertEqual(len(defaults), 1)
        self.assertEqual(defaults[0]["id"], self.subtitle_es.pk)

    def test_set_null_clears_default(self):
        self.content.default_subtitle_track = self.subtitle_en
        self.content.save()
        self.content.default_subtitle_track = None
        self.content.save()
        content = Content.objects.get(pk=self.content.pk)
        self.assertIsNone(content.default_subtitle_track)
        subtitles = content.get_subtitles()
        for sub in subtitles:
            self.assertFalse(sub["default"])


class UpdateContentDefaultSubtitleTrackTests(TestCase):
    """Tests for setting the default subtitle track through the update_content view."""

    def setUp(self):
        self.owner = UserFactory(instructor=True)
        self.resource = ResourceFactory()
        self.resource_file = ResourceFileFactory(resource=self.resource)
        self.playlist = PlaylistFactory(owner=self.owner)
        self.content = ContentFactory(
            playlist=self.playlist,
            resource_file=self.resource_file,
        )
        self.subtitle = SubtitleFactory(resource=self.resource, owner=self.owner)
        self.client.force_login(self.owner)

    def _post_update_content(self, **field_overrides):
        payload = {
            "id": self.content.pk,
            "title": self.content.title,
            "description": self.content.description,
            "words": self.content.words,
            "allow_definitions": self.content.allow_definitions,
            "allow_notes": self.content.allow_notes,
            "allow_captions": self.content.allow_captions,
            "allow_fast_playback": self.content.allow_fast_playback,
            "clips_only": self.content.clips_only,
            "published": self.content.published,
        }
        payload.update(field_overrides)
        return self.client.post(
            reverse("update_content"),
            data=json.dumps(payload),
            content_type="application/json",
        )

    def test_setting_default_subtitle_track_id_saves_it_on_content(self):
        response = self._post_update_content(default_subtitle_track_id=self.subtitle.pk)
        self.assertEqual(response.status_code, 200)
        content = Content.objects.get(pk=self.content.pk)
        self.assertEqual(content.default_subtitle_track_id, self.subtitle.pk)

    def test_omitting_default_subtitle_track_id_leaves_default_unset(self):
        response = self._post_update_content()
        self.assertEqual(response.status_code, 200)
        content = Content.objects.get(pk=self.content.pk)
        self.assertIsNone(content.default_subtitle_track_id)

    def test_sending_empty_default_subtitle_track_id_clears_existing_default(self):
        self.content.default_subtitle_track = self.subtitle
        self.content.save()

        response = self._post_update_content(default_subtitle_track_id="")

        self.assertEqual(response.status_code, 200)
        content = Content.objects.get(pk=self.content.pk)
        self.assertIsNone(content.default_subtitle_track_id)

    def test_subtitle_from_a_different_resource_is_not_saved_as_default(self):
        other_resource = ResourceFactory()
        subtitle_from_other_resource = SubtitleFactory(
            resource=other_resource, owner=self.owner
        )

        response = self._post_update_content(
            default_subtitle_track_id=subtitle_from_other_resource.pk
        )

        self.assertEqual(response.status_code, 200)
        content = Content.objects.get(pk=self.content.pk)
        self.assertIsNone(content.default_subtitle_track_id)
