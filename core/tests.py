# Create your tests here.
import copy
import unittest

from django.test import TestCase

from core.utils import VTTCue
from core.utils import build_cues_from_vtt_file_string
from core.utils import build_vtt_file_string_from_cues
from core.utils import nudge_cue_times
from core.utils import seconds2hms

from . import api


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
