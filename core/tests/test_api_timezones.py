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
