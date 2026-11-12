"""Production's side of force push and event notification: signed calls to a booth agent."""

import json
import logging
from urllib.parse import urlsplit

import requests

from .signing import signed_headers

logger = logging.getLogger(__name__)

TRIGGER_PATH = "/ic/agent/sync/"
TRIGGER_TIMEOUT_SECONDS = 5
PROGRESS_TIMEOUT_SECONDS = 3


class AgentError(Exception):
    """A booth agent call that failed, with a message an IC editor can act on."""


def _url(instance, path):
    return instance.agent_url.rstrip("/") + path


def _describe_connection_error(instance, exc):
    host = urlsplit(instance.agent_url).netloc or instance.agent_url
    text = str(exc)
    if "refused" in text.lower():
        return f"Booth unreachable: connection refused at {host}."
    if isinstance(exc, requests.Timeout):
        return f"Booth unreachable: no answer from {host} within a few seconds."
    return f"Booth unreachable at {host}: {text[:200]}"


def _check_response(response):
    if response.status_code in (401, 403):
        reason = response.text.strip()[:200] or "unauthorized"
        raise AgentError(f"Booth rejected the request: {reason}.")
    if response.status_code == 503:
        raise AgentError(
            response.text.strip()[:300] or "Booth sync agent isn't running."
        )
    if response.status_code >= 400:
        raise AgentError(
            f"Booth answered HTTP {response.status_code}: {response.text[:200]}"
        )


def send_trigger(
    instance, *, trigger, request_id, requested_by="", options=None, manifest_hash=""
):
    """Ask the booth to start a sync. Returns the booth's run id."""
    if not instance.can_be_pushed_to:
        raise AgentError(
            f"{instance.name} has no agent URL or no agent secret configured on production."
        )
    body = json.dumps(
        {
            "trigger": trigger,
            "request_id": str(request_id),
            "requested_by": requested_by,
            "options": options or {},
            "manifest_hash": manifest_hash,
        }
    ).encode()
    headers = {"Content-Type": "application/json"}
    headers.update(signed_headers(instance.agent_secret, "POST", TRIGGER_PATH, body))
    try:
        response = requests.post(
            _url(instance, TRIGGER_PATH),
            data=body,
            headers=headers,
            timeout=TRIGGER_TIMEOUT_SECONDS,
        )
    except requests.RequestException as exc:
        raise AgentError(_describe_connection_error(instance, exc)) from exc
    _check_response(response)
    return response.json()["run_id"]


def fetch_run(instance, run_id):
    """The booth's live progress for a run."""
    path = f"/ic/agent/sync/{run_id}/"
    headers = signed_headers(instance.agent_secret, "GET", path)
    try:
        response = requests.get(
            _url(instance, path), headers=headers, timeout=PROGRESS_TIMEOUT_SECONDS
        )
    except requests.RequestException as exc:
        raise AgentError(_describe_connection_error(instance, exc)) from exc
    _check_response(response)
    return response.json()
