"""The booth's HTTP client for production's sync API.

Every connection between the booth and production starts here, on the booth.
"""

import os
from pathlib import Path
import time

from django.conf import settings
import requests

READ_BYTES = 1024 * 1024
TIMEOUT_SECONDS = 30
CHECK_TIMEOUT_SECONDS = 10
# Downloads are always capped (a 10 GB master takes about 11 minutes), so disk and
# network I/O can't cause stutter on the projector whenever a sync happens to run.
MAX_BYTES_PER_SECOND = 15 * 1024 * 1024


class ParentError(Exception):
    """Production could not be reached or refused a request."""


class ParentClient:
    def __init__(self, base_url=None, token=None, session=None):
        self.base_url = (
            base_url if base_url is not None else settings.IC_PARENT_URL
        ).rstrip("/")
        self.session = session or requests.Session()
        self.session.headers["Authorization"] = (
            f"Bearer {token if token is not None else settings.IC_SYNC_TOKEN}"
        )

    def _request(self, method, path, **kwargs):
        if not self.base_url:
            raise ParentError("IC_PARENT_URL is not configured on this booth.")
        kwargs.setdefault("timeout", TIMEOUT_SECONDS)
        try:
            response = self.session.request(method, self.base_url + path, **kwargs)
        except requests.RequestException as exc:
            raise ParentError(f"Production unreachable: {exc}") from exc
        if response.status_code == 401:
            raise ParentError("Production rejected this booth's sync token.")
        return response

    def _get(self, path, **kwargs):
        return self._request("GET", path, **kwargs)

    def manifest(self):
        """Production's manifest (rows, files, fingerprints), with its "manifest_hash"."""
        response = self._get("/ic/sync/manifest/")
        if response.status_code != 200:
            raise ParentError(f"Manifest request failed: HTTP {response.status_code}")
        return response.json()

    def check(self):
        """{"manifest_hash": ...}: has production changed?"""
        response = self._get("/ic/sync/check/", timeout=CHECK_TIMEOUT_SECONDS)
        if response.status_code != 200:
            raise ParentError(f"Check failed: HTTP {response.status_code}")
        return response.json()

    def download(self, entry, part_path, *, on_bytes=None):
        """Download (resuming) into part_path until it reaches entry['size'] bytes.

        Each request asks for the rest of the file; production answers with a slice
        (core's range cap), and the loop continues from wherever it stopped.
        """
        part_path = Path(part_path)
        part_path.parent.mkdir(parents=True, exist_ok=True)
        expected = entry["size"]
        if entry["kind"] == "projection":
            path = f"/ic/sync/files/projection/{entry['resource_file_id']}/"
        else:
            path = f"/ic/sync/files/subtitle/{entry['subtitle_id']}/"
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
                response = self._get(
                    path, headers={"Range": f"bytes={offset}-"}, stream=True
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
                        minimum = received / MAX_BYTES_PER_SECOND
                        elapsed = time.monotonic() - started
                        if elapsed < minimum:
                            time.sleep(minimum - elapsed)
                except requests.RequestException as exc:
                    raise ParentError(f"Download interrupted: {exc}") from exc
                handle.flush()
                os.fsync(handle.fileno())
                if received == 0:
                    raise ParentError("Production sent no data.")

    def post_status(self, report):
        response = self._request("POST", "/ic/sync/status/", json=report)
        if response.status_code != 200:
            raise ParentError(f"Status report rejected: HTTP {response.status_code}")
