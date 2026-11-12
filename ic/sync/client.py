"""The booth's HTTP client for production's sync API.

Every connection between the booth and production starts here, on the booth.
"""

import os
from pathlib import Path
import time

from django.conf import settings
import requests

CHUNK_REQUEST_BYTES = 64 * 1024 * 1024
READ_BYTES = 1024 * 1024
TIMEOUT_SECONDS = 30
CHECK_TIMEOUT_SECONDS = 10
PROGRESS_TIMEOUT_SECONDS = 5
# While the output is playing, downloads are capped so disk and network I/O cannot
# cause stutter on the projector.
THROTTLED_BYTES_PER_SECOND = 15 * 1024 * 1024


class ParentError(Exception):
    """Production could not be reached or refused a request."""


class ParentClient:
    def __init__(self, base_url=None, token=None, session=None):
        self.base_url = (
            base_url if base_url is not None else settings.IC_PARENT_URL
        ).rstrip("/")
        self.token = token if token is not None else settings.IC_SYNC_TOKEN
        self.session = session or requests.Session()
        self.session.headers["Authorization"] = f"Bearer {self.token}"

    def _url(self, path):
        return f"{self.base_url}{path}"

    def _get(self, path, **kwargs):
        if not self.base_url:
            raise ParentError("IC_PARENT_URL is not configured on this booth.")
        kwargs.setdefault("timeout", TIMEOUT_SECONDS)
        try:
            response = self.session.get(self._url(path), **kwargs)
        except requests.RequestException as exc:
            raise ParentError(f"Production unreachable: {exc}") from exc
        if response.status_code == 401:
            raise ParentError("Production rejected this booth's sync token.")
        return response

    def manifest(self, if_none_match=None):
        """(manifest or None when unchanged, manifest_hash)."""
        headers = {"If-None-Match": f'"{if_none_match}"'} if if_none_match else {}
        response = self._get("/ic/sync/manifest/", headers=headers)
        if response.status_code == 304:
            return None, if_none_match
        if response.status_code != 200:
            raise ParentError(f"Manifest request failed: HTTP {response.status_code}")
        data = response.json()
        return data, data.get("manifest_hash", "")

    def check(self):
        """{"manifest_hash": ..., "sync_requests": [...]}: is there anything to do?"""
        response = self._get("/ic/sync/check/", timeout=CHECK_TIMEOUT_SECONDS)
        if response.status_code != 200:
            raise ParentError(f"Check failed: HTTP {response.status_code}")
        return response.json()

    def fixture(self):
        response = self._get("/ic/sync/fixture/")
        if response.status_code != 200:
            raise ParentError(f"Fixture request failed: HTTP {response.status_code}")
        return response.content

    def file_path(self, entry):
        if entry["kind"] == "projection":
            return f"/ic/sync/files/projection/{entry['resource_file_id']}/"
        return f"/ic/sync/files/subtitle/{entry['subtitle_id']}/"

    def download(self, entry, part_path, *, on_bytes=None, throttled=lambda: False):
        """Download (resuming) into part_path until it reaches entry['size'] bytes."""
        part_path = Path(part_path)
        part_path.parent.mkdir(parents=True, exist_ok=True)
        expected = entry["size"]
        path = self.file_path(entry)
        if entry["kind"] != "projection" or not expected:
            response = self._get(path)
            if response.status_code != 200:
                raise ParentError(f"Download failed: HTTP {response.status_code}")
            part_path.write_bytes(response.content)
            if on_bytes:
                on_bytes(len(response.content))
            return
        offset = part_path.stat().st_size if part_path.exists() else 0
        if offset > expected:
            part_path.unlink()
            offset = 0
        with open(part_path, "ab") as handle:
            while offset < expected:
                end = min(expected, offset + CHUNK_REQUEST_BYTES) - 1
                response = self._get(
                    path, headers={"Range": f"bytes={offset}-{end}"}, stream=True
                )
                if response.status_code == 404:
                    raise ParentError("Production no longer holds this file.")
                if response.status_code not in (200, 206):
                    raise ParentError(f"Download failed: HTTP {response.status_code}")
                if response.status_code == 200 and offset:
                    raise ParentError("Production ignored the Range request.")
                started = time.monotonic()
                received = 0
                try:
                    for chunk in response.iter_content(READ_BYTES):
                        handle.write(chunk)
                        received += len(chunk)
                        offset += len(chunk)
                        if on_bytes:
                            on_bytes(len(chunk))
                        if throttled():
                            minimum = received / THROTTLED_BYTES_PER_SECOND
                            elapsed = time.monotonic() - started
                            if elapsed < minimum:
                                time.sleep(minimum - elapsed)
                except requests.RequestException as exc:
                    raise ParentError(f"Download interrupted: {exc}") from exc
                handle.flush()
                os.fsync(handle.fileno())
                if received == 0:
                    raise ParentError("Production sent no data.")

    def _post(self, path, data, timeout):
        if not self.base_url:
            raise ParentError("IC_PARENT_URL is not configured on this booth.")
        try:
            response = self.session.post(self._url(path), json=data, timeout=timeout)
        except requests.RequestException as exc:
            raise ParentError(f"Production unreachable: {exc}") from exc
        if response.status_code != 200:
            raise ParentError(f"{path} rejected: HTTP {response.status_code}")

    def post_status(self, report):
        self._post("/ic/sync/status/", report, TIMEOUT_SECONDS)

    def post_progress(self, progress):
        self._post("/ic/sync/progress/", progress, PROGRESS_TIMEOUT_SECONDS)
