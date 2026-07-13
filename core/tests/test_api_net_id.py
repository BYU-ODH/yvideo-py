from unittest.mock import Mock
from unittest.mock import patch

from django.test import TestCase

from core import api as api_module
from core.api import Api
from core.models import AuthToken


class GetNetIdFromWorkerIdTests(TestCase):
    def _build_api(self):
        # Pre-seed a valid token so Api() does not attempt to reach the network.
        AuthToken.objects.create(token="existing-token")
        return Api()

    @patch("core.api.requests.get")
    def test_returns_net_id_from_response(self, mock_get):
        api = self._build_api()

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"data": [{"net_id": "jdoe42"}]}
        mock_get.return_value = mock_response

        with patch.object(
            api_module.secret_settings,
            "API_NET_ID_IAM_URL",
            "https://example.invalid/netid",
            create=True,
        ):
            result = api.get_net_id_from_worker_id("W123")

        self.assertEqual(result, "jdoe42")

    @patch("core.api.requests.get")
    def test_returns_none_on_non_200_response(self, mock_get):
        api = self._build_api()

        mock_response = Mock()
        mock_response.status_code = 500
        mock_get.return_value = mock_response

        with patch.object(
            api_module.secret_settings,
            "API_NET_ID_IAM_URL",
            "https://example.invalid/netid",
            create=True,
        ):
            result = api.get_net_id_from_worker_id("W123")

        self.assertIsNone(result)
        # A non-200 short-circuits before the body is ever read.
        mock_response.json.assert_not_called()

    @patch("core.api.requests.get")
    def test_returns_none_when_no_net_id_in_data(self, mock_get):
        api = self._build_api()

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"data": []}
        mock_get.return_value = mock_response

        with patch.object(
            api_module.secret_settings,
            "API_NET_ID_IAM_URL",
            "https://example.invalid/netid",
            create=True,
        ):
            result = api.get_net_id_from_worker_id("W123")

        self.assertIsNone(result)
