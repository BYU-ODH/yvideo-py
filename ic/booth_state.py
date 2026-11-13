"""The sync agent's heartbeat, a tiny file the booth's containers share.

Kept out of the database on purpose: the agent writes it every second or so, and the
kiosk (which is read-only) only reads it.
"""

import json
import os
from pathlib import Path
import time

from django.conf import settings

AGENT_FILE = "agent.json"

AGENT_ALIVE_SECONDS = 30


def _dir():
    path = Path(settings.IC_STATE_DIR)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _write(name, data):
    path = _dir() / name
    temp = path.with_suffix(f".{os.getpid()}.tmp")
    temp.write_text(json.dumps(data))
    os.replace(temp, path)


def _read(name):
    try:
        return json.loads((_dir() / name).read_text())
    except (OSError, ValueError):
        return None


def record_agent_heartbeat(now=None, **extra):
    _write(AGENT_FILE, {"at": now if now is not None else time.time(), **extra})


def agent_heartbeat():
    return _read(AGENT_FILE)


def agent_alive(now=None, within=AGENT_ALIVE_SECONDS):
    data = agent_heartbeat()
    if not data:
        return False
    now = now if now is not None else time.time()
    return now - data.get("at", 0) <= within


AGENT_DOWN_MESSAGE = "Sync agent isn't running; restart it with `systemctl --user restart ic-sync-agent`."
