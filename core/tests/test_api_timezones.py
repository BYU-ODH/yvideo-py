from datetime import timedelta
from unittest.mock import Mock
from unittest.mock import patch
import warnings

from django.test import TestCase
from django.utils import timezone

from core import api as api_module
from core.api import Api
from core.models import AuthToken


class ApiTimezoneTests(TestCase):
    def test_parse_api_datetime_returns_aware_datetime(self):
        parsed = Api.parse_api_datetime("2026-03-27T12:34:56")

        self.assertTrue(timezone.is_aware(parsed))

    def test_api_init_does_not_emit_naive_datetime_warning_for_valid_token(self):
        AuthToken.objects.create(token="existing-token")

        with patch.object(
            Api, "generate_auth_token", return_value="fresh-token"
        ) as mock_generate:
            with warnings.catch_warnings(record=True) as caught_warnings:
                warnings.simplefilter("always")
                api = Api()

        self.assertEqual(api.auth_token, "existing-token")
        mock_generate.assert_not_called()
        self.assertFalse(
            any(
                "naive datetime" in str(warning.message).lower()
                for warning in caught_warnings
            )
        )

    @patch("core.api.requests.get")
    def test_get_current_year_term_uses_aware_api_datetimes(self, mock_get):
        AuthToken.objects.create(token="existing-token")
        api = Api()
        local_now = timezone.localtime()

        mock_response = Mock()
        mock_response.json.return_value = {
            "data": [
                {
                    "year_term": "20263",
                    "start_date_time": (local_now - timedelta(days=1)).strftime(
                        "%Y-%m-%dT%H:%M:%S"
                    ),
                    "end_date_time": (local_now + timedelta(days=10)).strftime(
                        "%Y-%m-%dT%H:%M:%S"
                    ),
                }
            ]
        }
        mock_get.return_value = mock_response

        with patch.object(
            api_module.secret_settings,
            "API_YEARTERM_URL",
            "https://example.invalid/yearterm",
            create=True,
        ):
            result = api.get_current_year_term()

        self.assertEqual(result["yearterm"], "20263")
        self.assertTrue(result["is_two_weeks_from_end"])


class GetYearTermsTests(TestCase):
    """get_year_terms is what feeds the YearTerm cache the permission checks read."""

    def setUp(self):
        AuthToken.objects.create(token="existing-token")

    def api_returning(self, entries):
        api = Api()
        mock_response = Mock()
        mock_response.json.return_value = {"data": entries}
        return api, mock_response

    @patch("core.api.requests.get")
    def test_returns_every_term_with_parsed_dates(self, mock_get):
        api, mock_response = self.api_returning(
            [
                {
                    "year_term": "20265",
                    "start_date_time": "2026-09-01T00:00:00",
                    "end_date_time": "2026-12-15T00:00:00",
                },
                {
                    "year_term": "20271",
                    "start_date_time": "2027-01-05T00:00:00",
                    "end_date_time": "2027-04-20T00:00:00",
                },
            ]
        )
        mock_get.return_value = mock_response

        year_terms = api.get_year_terms()

        self.assertEqual(
            [entry["yearterm"] for entry in year_terms], ["20265", "20271"]
        )
        for entry in year_terms:
            self.assertTrue(timezone.is_aware(entry["start_date_time"]))
            self.assertTrue(timezone.is_aware(entry["end_date_time"]))

    @patch("core.api.requests.get")
    def test_an_unparseable_entry_is_skipped_rather_than_raising(self, mock_get):
        api, mock_response = self.api_returning(
            [
                {"year_term": "20265", "start_date_time": "not a date"},
                {
                    "year_term": "20271",
                    "start_date_time": "2027-01-05T00:00:00",
                    "end_date_time": "2027-04-20T00:00:00",
                },
            ]
        )
        mock_get.return_value = mock_response

        year_terms = api.get_year_terms()

        self.assertEqual([entry["yearterm"] for entry in year_terms], ["20271"])


class RefreshYearTermsCommandTests(TestCase):
    def setUp(self):
        AuthToken.objects.create(token="existing-token")

    @patch("core.management.commands.refresh_year_terms.Api")
    def test_caches_and_updates_terms(self, mock_api):
        from django.core.management import call_command

        from core.models import YearTerm

        start = timezone.now()
        mock_api.return_value.get_year_terms.return_value = [
            {
                "yearterm": "20265",
                "start_date_time": start,
                "end_date_time": start + timedelta(days=100),
            }
        ]

        call_command("refresh_year_terms")
        self.assertEqual(YearTerm.objects.count(), 1)

        # A second run updates in place rather than duplicating.
        mock_api.return_value.get_year_terms.return_value[0]["end_date_time"] = (
            start + timedelta(days=110)
        )
        call_command("refresh_year_terms")

        self.assertEqual(YearTerm.objects.count(), 1)
        self.assertEqual(
            YearTerm.objects.get().end_date_time, start + timedelta(days=110)
        )

    @patch("core.management.commands.refresh_year_terms.Api")
    def test_an_empty_api_response_leaves_the_cache_alone(self, mock_api):
        from django.core.management import call_command
        from django.core.management.base import CommandError

        from core.models import YearTerm

        existing = YearTerm.objects.create(
            yearterm="20265",
            start_date_time=timezone.now(),
            end_date_time=timezone.now() + timedelta(days=100),
        )
        mock_api.return_value.get_year_terms.return_value = []

        with self.assertRaises(CommandError):
            call_command("refresh_year_terms")

        self.assertTrue(YearTerm.objects.filter(pk=existing.pk).exists())
