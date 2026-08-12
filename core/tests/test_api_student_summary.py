from unittest.mock import Mock
from unittest.mock import patch

from django.test import TestCase
import requests

from core import api as api_module
from core.api import Api
from core.api import ApiUnavailable
from core.models import AuthToken

STUDENT_RECORD = {
    "preferred_name": "Sam Q",
    "preferred_last_name": "Student",
    "student_email_address": "sam@byu.edu",
    "byu_id": "123456789",
    "net_id": "samstud",
}


class GetStudentSummaryTests(TestCase):
    """A found record, a genuine "no record", and a failed request have to be
    three distinguishable outcomes: the admin add-user form tells an admin
    whether to retry or to fall back to a BYU ID based on which one it got."""

    def _build_api(self):
        # Pre-seed a valid token so Api() does not attempt to reach the network.
        AuthToken.objects.create(token="existing-token")
        return Api()

    def _patched_url(self):
        return patch.object(
            api_module.secret_settings,
            "API_STUDENT_SUMMARY_URL",
            "https://example.invalid/student/",
            create=True,
        )

    @patch("core.api.requests.get")
    def test_returns_parsed_summary_for_a_student(self, mock_get):
        api = self._build_api()
        mock_get.return_value = Mock(
            status_code=200, json=Mock(return_value={"data": [STUDENT_RECORD]})
        )

        with self._patched_url():
            summary = api.get_student_summary(net_id="samstud")

        self.assertEqual(summary["first_name"], "Sam")
        self.assertEqual(summary["last_name"], "Student")
        self.assertEqual(summary["byu_id"], "123456789")
        self.assertEqual(summary["net_id"], "samstud")

    @patch("core.api.requests.get")
    def test_returns_none_when_the_api_has_no_record(self, mock_get):
        api = self._build_api()
        mock_get.return_value = Mock(
            status_code=200, json=Mock(return_value={"data": []})
        )

        with self._patched_url():
            self.assertIsNone(api.get_student_summary(net_id="nonstudent"))

    @patch("core.api.requests.get")
    def test_raises_on_non_200_response(self, mock_get):
        api = self._build_api()
        mock_response = Mock(status_code=503)
        mock_get.return_value = mock_response

        with self._patched_url(), self.assertRaises(ApiUnavailable):
            api.get_student_summary(net_id="samstud")

        # A non-200 short-circuits before the body is ever read.
        mock_response.json.assert_not_called()

    @patch("core.api.requests.get")
    def test_raises_when_the_response_body_cannot_be_read(self, mock_get):
        api = self._build_api()
        mock_get.return_value = Mock(
            status_code=200,
            json=Mock(return_value={"info": {"errors": ["nope"]}}),
        )

        with self._patched_url(), self.assertRaises(ApiUnavailable):
            api.get_student_summary(net_id="samstud")

    @patch("core.api.requests.get")
    def test_raises_when_the_request_cannot_be_sent(self, mock_get):
        api = self._build_api()
        mock_get.side_effect = requests.ConnectionError("no route to host")

        with self._patched_url(), self.assertRaises(ApiUnavailable):
            api.get_student_summary(net_id="samstud")
