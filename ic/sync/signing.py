"""HMAC request signing for production → booth agent calls (IC_MODE_PLAN.md section 7.7).

The signature covers the timestamp, method, path and a hash of the body, so a captured
request cannot be altered, and requests older than MAX_SKEW_SECONDS are refused. Replays
inside that window are harmless because triggers carry an idempotent request_id.
"""

import hashlib
import hmac
import time

MAX_SKEW_SECONDS = 60
TIMESTAMP_HEADER = "X-IC-Timestamp"
SIGNATURE_HEADER = "X-IC-Signature"


def signature(secret, timestamp, method, path, body=b""):
    body_hash = hashlib.sha256(body or b"").hexdigest()
    message = f"{timestamp}\n{method.upper()}\n{path}\n{body_hash}".encode()
    return hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()


def signed_headers(secret, method, path, body=b"", now=None):
    timestamp = str(int(now if now is not None else time.time()))
    return {
        TIMESTAMP_HEADER: timestamp,
        SIGNATURE_HEADER: signature(secret, timestamp, method, path, body),
    }


def verify(secret, timestamp, provided, method, path, body=b"", now=None):
    """None if the request is authentic, else a short reason."""
    if not secret:
        return "this instance has no agent secret configured"
    if not timestamp or not provided:
        return "missing signature"
    try:
        sent_at = int(timestamp)
    except ValueError:
        return "bad timestamp"
    current = now if now is not None else time.time()
    if abs(current - sent_at) > MAX_SKEW_SECONDS:
        return "timestamp outside the allowed window (check both clocks)"
    expected = signature(secret, timestamp, method, path, body)
    if not hmac.compare_digest(expected, provided):
        return "bad signature"
    return None
