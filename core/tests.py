# Create your tests here.
import copy

from django.test import TestCase

from core.utils import VTTCue
from core.utils import build_cues_from_vtt_file_string
from core.utils import build_vtt_file_string_from_cues
from core.utils import nudge_cue_times

from . import api
from .utils import seconds2hms


class ApiTests(TestCase):
    def test_build_auth_header(self):
        new_api = api.Api()
        re = r"Bearer[ ]\S*"
        result = new_api.build_auth_header()
        self.assertRegex(result, re)

    def test_get_current_year_term(self):
        re = r"[0-9]{4}[1-6]"
        new_api = api.Api()
        result = new_api.get_current_year_term()
        self.assertRegex(result["yearterm"], re)

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
