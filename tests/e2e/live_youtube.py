"""Deciding whether the real YouTube API can be exercised from wherever the tests are running.

Three separate things have to be true before a live-API test means anything, and none of them is a
statement about this repository:

1. youtube.com is reachable at all.
2. The video the tests embed is still published and still embeddable.
3. This machine is allowed to *play* it.

The third is the one that surprises people. Datacenter and bot-flagged IP ranges - GitHub Actions
runners among them - answer 1 and 2 happily: the API script loads, the player reports metadata and a
real duration, and then playback simply never starts. Sometimes an error arrives after the metadata,
in which case YouTubeVideoElement replaces the iframe with its "can't be played here" notice and a
test waiting for that iframe times out instead of saying why.

So the checks come in two kinds. The HTTP ones here answer 1 and 2 before a browser is involved; the
`require_live_embed` fixture in conftest.py answers 3 by watching what the player actually does.
Both skip rather than fail, and say which of the three gave out.
"""

import functools
import os
import urllib.request

# The video the live-API tests embed. One id, in one place, so the reachability check and the tests
# it guards can never be asking about different videos.
LIVE_YOUTUBE_VIDEO_ID = "eHEsJyVQn3w"

_IFRAME_API_URL = "https://www.youtube.com/iframe_api"
_OEMBED_URL = (
    "https://www.youtube.com/oembed?format=json"
    f"&url=https://www.youtube.com/watch%3Fv%3D{LIVE_YOUTUBE_VIDEO_ID}"
)
# Long enough to survive a slow link, short enough that an offline machine is not punished for it.
_NETWORK_TIMEOUT_SECONDS = 8

# Generous: a cold embed on a loaded CI runner is slower than anything a developer sees.
LIVE_EMBED_TIMEOUT_MS = 25_000
# A refusal typically arrives just *after* the metadata does, so waiting only for the iframe would
# happily return a moment before the player gives up. This is how long to keep watching afterwards.
LIVE_EMBED_SETTLE_MS = 2_000

# `error` is what YouTubeVideoElement sets when the player reports it cannot play this - after which
# it replaces the iframe with its notice, so an iframe check alone would fail on a null element
# rather than explain itself.
EMBED_OUTCOME_JS = """() => {
    const element = document.querySelector('youtube-video');
    if (!element) return null;
    if (element.error) return 'refused';
    return element.querySelector('iframe') && element.duration > 0 ? 'loaded' : null;
}"""

# Shared by every guard that decides the embed cannot run here, so one explanation covers all of
# them however the refusal presented itself.
LIVE_EMBED_REFUSED_MESSAGE = (
    "youtube.com answered but will not play this embed from this machine. Datacenter and "
    "bot-flagged IP ranges - GitHub Actions runners among them - give metadata and a duration and "
    "then nothing: playback never starts, or the player raises an error. That is a fact about where "
    "this test is running, not about this code, and the fake-backed YouTube tests still ran"
)


@functools.cache
def unavailable_reason():
    """Why the live-API tests cannot run here, or None if they can.

    oembed is the cheap way to ask whether the video still exists: it answers 200 only while the
    video is published and embeddable, which is the state these tests need it to be in.

    Cached, because it is a network round trip and every live test asks.
    """
    if os.environ.get("SKIP_LIVE_YOUTUBE_TESTS"):
        return "SKIP_LIVE_YOUTUBE_TESTS is set"

    checks = (
        (_IFRAME_API_URL, "youtube.com is unreachable"),
        (
            _OEMBED_URL,
            f"the pinned video {LIVE_YOUTUBE_VIDEO_ID} is gone or no longer embeddable",
        ),
    )
    for url, failure in checks:
        request = urllib.request.Request(url, headers={"User-Agent": "yvideo-tests"})
        try:
            with urllib.request.urlopen(
                request, timeout=_NETWORK_TIMEOUT_SECONDS
            ) as response:
                if response.status != 200:
                    return f"{failure} (HTTP {response.status})"
        # HTTPError included: urllib raises rather than returns for a 404, and a 404 from oembed is
        # the takedown case this exists to report.
        except OSError as exc:
            return f"{failure} ({exc})"
    return None
