from unittest.mock import Mock
from unittest.mock import patch

from django.test import TestCase

from core import api as api_module
from core.api import Api
from core.models import AuthToken

ENROLLMENT_RECORD = {
    "curriculum_id": "01234",
    "title_code": "001",
    "section_number": "001",
    "teaching_area": "SPAN",
    "catalog_number": "101",
    "catalog_suffix": "",
    "credit_hours": "3.0",
    "withdraw_flag": "N",
    "audit_flag": "N",
}


class GetStudentEnrollmentsTests(TestCase):
    """An empty list and a failed lookup mean different things to the caller.

    update_user_enrollment revokes course access on an empty list, so anything it
    cannot trust has to arrive as None instead.
    """

    def _build_api(self):
        # Pre-seed a valid token so Api() does not attempt to reach the network.
        AuthToken.objects.create(token="existing-token")
        return Api()

    def _fetch(self, api, response):
        with (
            patch("core.api.requests.get", return_value=response),
            patch.object(
                api_module.secret_settings,
                "API_STUDENT_ENROLLMENTS_URL",
                "https://example.invalid/enrollments",
                create=True,
            ),
        ):
            return api.get_student_enrollments("tstudent", "20265")

    def _response(self, status_code=200, payload=None):
        response = Mock()
        response.status_code = status_code
        response.json.return_value = payload
        return response

    def test_returns_the_parsed_records(self):
        api = self._build_api()

        result = self._fetch(api, self._response(payload={"data": [ENROLLMENT_RECORD]}))

        self.assertEqual(result, [ENROLLMENT_RECORD])

    def test_no_enrollments_is_an_empty_list_not_none(self):
        api = self._build_api()

        result = self._fetch(api, self._response(payload={"data": []}))

        self.assertEqual(result, [])

    def test_a_null_data_field_is_an_empty_list(self):
        api = self._build_api()

        result = self._fetch(api, self._response(payload={"data": None}))

        self.assertEqual(result, [])

    def test_returns_none_on_non_200_response(self):
        api = self._build_api()

        result = self._fetch(api, self._response(status_code=503))

        self.assertIsNone(result)

    def test_returns_none_when_the_payload_has_no_data_field(self):
        api = self._build_api()

        result = self._fetch(api, self._response(payload={"error": "nope"}))

        self.assertIsNone(result)

    def test_an_unreadable_record_fails_the_whole_lookup(self):
        api = self._build_api()
        incomplete = dict(ENROLLMENT_RECORD)
        del incomplete["section_number"]

        result = self._fetch(
            api, self._response(payload={"data": [ENROLLMENT_RECORD, incomplete]})
        )

        self.assertIsNone(result)
