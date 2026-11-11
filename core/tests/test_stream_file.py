"""stream_file status codes and response shapes (#376)."""

import os
import shutil
import tempfile
import time
from unittest import mock

from django.http import StreamingHttpResponse
from django.test import TestCase
from django.test import override_settings
from django.urls import reverse

from core.factories import ResourceFileFactory
from core.factories import ResourceFileKeyFactory
from core.factories import UserFactory

PAYLOAD = b"0123456789abcdef"


def login(client, user):
    client.force_login(user)
    session = client.session
    session["oidc_id_token_expiration"] = time.time() + 3600
    session.save()


class StreamFileTests(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._media_root = tempfile.mkdtemp(prefix="stream-file-media-")
        cls._settings = override_settings(MEDIA_ROOT=cls._media_root)
        cls._settings.enable()

    @classmethod
    def tearDownClass(cls):
        cls._settings.disable()
        shutil.rmtree(cls._media_root, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        self.user = UserFactory(student=True)
        login(self.client, self.user)
        self.resource_file = ResourceFileFactory()
        with open(self.resource_file.file.path, "wb") as f:
            f.write(PAYLOAD)
        self.key = ResourceFileKeyFactory(
            user=self.user, resource_file=self.resource_file
        )
        self.url = reverse("stream_file", args=[self.key.pk])

    def test_full_file_streams(self):
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertIsInstance(response, StreamingHttpResponse)
        self.assertEqual(b"".join(response.streaming_content), PAYLOAD)
        self.assertEqual(response["Content-Length"], str(len(PAYLOAD)))
        self.assertEqual(response["Content-Type"], "video/mp4")
        self.assertEqual(response["Accept-Ranges"], "bytes")

    def test_range_request_returns_partial_content(self):
        response = self.client.get(self.url, HTTP_RANGE="bytes=2-5")

        self.assertEqual(response.status_code, 206)
        self.assertEqual(response.content, PAYLOAD[2:6])
        self.assertEqual(response["Content-Range"], f"bytes 2-5/{len(PAYLOAD)}")
        self.assertEqual(response["Content-Length"], "4")
        self.assertEqual(response["Content-Type"], "video/mp4")

    def test_open_ended_range_runs_to_end(self):
        response = self.client.get(self.url, HTTP_RANGE="bytes=10-")

        self.assertEqual(response.status_code, 206)
        self.assertEqual(response.content, PAYLOAD[10:])

    def test_unsatisfiable_range_is_416(self):
        response = self.client.get(self.url, HTTP_RANGE=f"bytes={len(PAYLOAD)}-")

        self.assertEqual(response.status_code, 416)
        self.assertEqual(response["Content-Range"], f"bytes */{len(PAYLOAD)}")

    def test_unknown_key_is_404(self):
        response = self.client.get(reverse("stream_file", args=[self.key.pk + 1000]))

        self.assertEqual(response.status_code, 404)

    def test_missing_file_on_disk_is_404_without_leaking_path(self):
        path = self.resource_file.file.path
        os.remove(path)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 404)
        self.assertNotIn(path.encode(), response.content)

    def test_unexpected_error_is_logged_and_generic(self):
        secret = "/srv/media/secret/path.mp4"
        with (
            mock.patch(
                "core.views._file_response", side_effect=OSError(f"boom {secret}")
            ),
            self.assertLogs("core.views", level="ERROR") as logs,
        ):
            response = self.client.get(self.url)

        self.assertEqual(response.status_code, 500)
        self.assertNotIn(secret.encode(), response.content)
        self.assertIn(secret, "\n".join(logs.output))
