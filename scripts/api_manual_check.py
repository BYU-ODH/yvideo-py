#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.12"
# dependencies = [
#   "requests>=2.32,<3",
# ]
# ///
"""Manually test BYU OIT API endpoints using credentials from yvideo/secret_settings.py.

Usage:
    uv run scripts/api_test.py <endpoint> [options]

Endpoints:
    enrollment  --netid=<netid> --year_term=<yearterm>
                Fetch a student's enrollment records for a given term.
                yearterm format: YYYYT where T is 1=Winter, 3=Spring, 4=Summer, 5=Fall
                Example: uv run scripts/api_test.py enrollment --netid=jsmith --year_term=20265

    student     --netid=<netid>
                Fetch a student's summary by net ID.
                Example: uv run scripts/api_test.py student --netid=jsmith

    yearterms   Fetch all yearterms known to the API.
                Example: uv run scripts/api_test.py yearterms
"""

import argparse
import importlib.util
import json
from pathlib import Path
import sys

import requests

REPO_ROOT = Path(__file__).resolve().parent.parent
SECRET_SETTINGS_PATH = REPO_ROOT / "yvideo" / "secret_settings.py"
SECRET_SETTINGS_TEMPLATE_PATH = REPO_ROOT / "yvideo" / "secret_settings_template.py"


def load_settings():
    path = (
        SECRET_SETTINGS_PATH
        if SECRET_SETTINGS_PATH.exists()
        else SECRET_SETTINGS_TEMPLATE_PATH
    )
    spec = importlib.util.spec_from_file_location("secret_settings", path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"Could not load settings from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def get_token(settings):
    if not settings.API_AUTH_TOKEN_URL:
        raise SystemExit("API_AUTH_TOKEN_URL is not set in secret_settings.py")
    resp = requests.post(
        settings.API_AUTH_TOKEN_URL,
        data={"grant_type": "client_credentials"},
        auth=(settings.API_CLIENT_ID, settings.API_CLIENT_SECRET),
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


def print_response(resp):
    print(f"Status: {resp.status_code}")
    try:
        print(json.dumps(resp.json(), indent=2))
    except ValueError:
        print(resp.text)


def cmd_enrollment(settings, token, args):
    parser = argparse.ArgumentParser(prog="api_test.py enrollment", add_help=False)
    parser.add_argument("--netid", required=True)
    parser.add_argument("--year_term", required=True)
    try:
        opts = parser.parse_args(args)
    except SystemExit:
        print(__doc__)
        raise SystemExit(1)

    if not settings.API_STUDENT_ENROLLMENTS_URL:
        raise SystemExit("API_STUDENT_ENROLLMENTS_URL is not set in secret_settings.py")

    url = f"{settings.API_STUDENT_ENROLLMENTS_URL}?net_id={opts.netid}&year_term={opts.year_term}"
    resp = requests.get(url, headers={"Authorization": f"Bearer {token}"})
    print_response(resp)


def cmd_student(settings, token, args):
    parser = argparse.ArgumentParser(prog="api_test.py student", add_help=False)
    parser.add_argument("--netid", required=True)
    try:
        opts = parser.parse_args(args)
    except SystemExit:
        print(__doc__)
        raise SystemExit(1)

    if not settings.API_STUDENT_SUMMARY_URL:
        raise SystemExit("API_STUDENT_SUMMARY_URL is not set in secret_settings.py")

    url = f"{settings.API_STUDENT_SUMMARY_URL}?net_id={opts.netid}"
    resp = requests.get(url, headers={"Authorization": f"Bearer {token}"})
    print_response(resp)


def cmd_yearterms(settings, token, _args):
    if not settings.API_YEARTERM_URL:
        raise SystemExit("API_YEARTERM_URL is not set in secret_settings.py")

    resp = requests.get(
        settings.API_YEARTERM_URL, headers={"Authorization": f"Bearer {token}"}
    )
    print_response(resp)


COMMANDS = {
    "enrollment": cmd_enrollment,
    "student": cmd_student,
    "yearterms": cmd_yearterms,
}


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        print(__doc__)
        sys.exit(1)

    endpoint = sys.argv[1]
    remaining = sys.argv[2:]

    settings = load_settings()
    token = get_token(settings)
    COMMANDS[endpoint](settings, token, remaining)


if __name__ == "__main__":
    main()
