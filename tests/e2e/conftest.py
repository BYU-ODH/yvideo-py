import functools
from io import StringIO
import os
import urllib.request

from django.core.management import call_command
import pytest

# pytest-playwright initializes an event loop before Django's test DB setup.
# These browser tests intentionally use Django's sync ORM and live server.
os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")

# Seeded by core/dev_seed.py, and the subject of nearly every test here: it carries the real
# birds.mp4, one clip, and the seeded blurs.
DEMO_CONTENT_TITLE = "Birds Overview"

VIDEO_SELECTOR = ".annotation-player-container video"

# Generous on purpose: this is a cold page load in a real browser, and a shorter timeout does not
# make a passing test faster, it only makes a slow machine report a flake instead of a failure.
READY_TIMEOUT_MS = 10_000

# window.videoPlayer and the video's duration load via two independent async paths with no ordering
# guarantee between them, so waiting on duration alone races window.videoPlayer.loadData, which
# several tests call immediately.
_PLAYER_READY = """(video) => {
    const element = document.querySelector(video);
    return Boolean(window.videoPlayer && element && !isNaN(element.duration)
        && element.duration > 0);
}"""

# The editor additionally has to have drawn its timeline: renderTickMarksAndLabels and
# attachTimelineListeners run together in init, so ticks on screen means the listeners every
# editor test drives are attached.
_EDITOR_READY = """(video) => {
    const element = document.querySelector(video);
    const ticks = document.getElementById('tick-marks-container');
    return Boolean(window.videoPlayer && element && !isNaN(element.duration)
        && element.duration > 0 && ticks && ticks.children.length > 0);
}"""


@pytest.fixture
def seeded_demo_data(settings, tmp_path, transactional_db):
    media_root = tmp_path / "media"
    media_root.mkdir()

    settings.DEBUG = True
    settings.DEV_QUICK_LOGIN_ENABLED = True
    settings.MEDIA_ROOT = media_root
    settings.ALLOWED_HOSTS = ["localhost", "127.0.0.1", "testserver"]
    settings.SECRET_KEY = "test-secret-key"

    call_command("seed_demo_data", stdout=StringIO())

    return media_root


# The video the live-API tests embed. One id, in one place, so the reachability check below and
# the tests it guards can never be asking about different videos.
LIVE_YOUTUBE_VIDEO_ID = "eHEsJyVQn3w"

_IFRAME_API_URL = "https://www.youtube.com/iframe_api"
_OEMBED_URL = (
    "https://www.youtube.com/oembed?format=json"
    f"&url=https://www.youtube.com/watch%3Fv%3D{LIVE_YOUTUBE_VIDEO_ID}"
)
# Long enough to survive a slow link, short enough that an offline machine is not punished for it.
_NETWORK_TIMEOUT_SECONDS = 8


@functools.cache
def _live_youtube_unavailable_reason():
    """Why the live-API tests cannot run here, or None if they can.

    Two things can stop them that are not regressions in this repo: no network, and someone else
    unpublishing the video we embed. Neither should fail a run, and both should say so in words
    rather than as an assertion about our own code - which is what an unguarded live test reports.

    oembed is the cheap way to ask the second question: it answers 200 only while the video is
    published and embeddable, which is exactly the state these tests need it to be in.

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


@pytest.fixture(scope="session")
def live_youtube():
    """The live video id, or a skip explaining why the real API cannot be reached.

    Request this from any test that talks to youtube.com, so an offline machine reports "skipped:
    youtube.com is unreachable" instead of a failure that looks like a bug here.
    """
    reason = _live_youtube_unavailable_reason()
    if reason:
        pytest.skip(f"needs the live YouTube API: {reason}")
    return LIVE_YOUTUBE_VIDEO_ID


@pytest.fixture
def fake_youtube(page):
    """Install the stand-in IFrame Player API before any navigation.

    Request this in place of reaching youtube.com. See tests/e2e/fake_youtube.py for what it does
    and does not stand in for.

    Also asserts that nothing in the test reached youtube.com, which is the point of the fake: the
    stub only avoids the network as long as YouTubeVideoElement keeps checking for an existing
    `window.YT` before injecting the API script, and a silent regression there would leave these
    tests quietly network-dependent again.
    """
    from tests.e2e.fake_youtube import install_fake_youtube

    install_fake_youtube(page)
    reached_youtube = []

    def _record(request):
        if "youtube.com" in request.url or "youtu.be" in request.url:
            reached_youtube.append(request.url)

    page.on("request", _record)
    yield page
    page.remove_listener("request", _record)
    assert not reached_youtube, (
        "these tests are meant to run without the network, but the page requested: "
        f"{reached_youtube}"
    )


@pytest.fixture
def youtube_content(seeded_demo_data):
    """A YouTube-backed Content in the demo playlist, created the way the app creates one."""
    from core.models import Content
    from core.models import Playlist
    from core.youtube_utils import get_or_create_youtube_resource

    def _create(title="YouTube Content", video_id="eHEsJyVQn3w"):
        playlist = Playlist.objects.get(name="Local Admin / Demo Review Shelf")
        resource = get_or_create_youtube_resource(video_id, playlist.owner.username)
        return Content.objects.create(
            playlist=playlist,
            title=title,
            url=f"https://www.youtube.com/watch?v={video_id}",
            resource=resource,
        )

    return _create


@pytest.fixture
def demo_content(seeded_demo_data):
    """The Content nearly every test here navigates to."""
    from core.models import Content

    return Content.objects.get(title=DEMO_CONTENT_TITLE)


@pytest.fixture
def logged_in_page(page, live_server, seeded_demo_data):
    """A page authenticated as the demo admin, who can view and edit the seeded content.

    Requests seeded_demo_data because dev-quick-login only exists when it has set
    DEV_QUICK_LOGIN_ENABLED, and it has to have run before the login is attempted.
    """
    page.goto(f"{live_server.url}/login/dev/quick/")
    return page


@pytest.fixture
def open_player(logged_in_page, live_server, demo_content):
    """Open the player on a content and wait until it is ready to be driven.

    Returns the content, so tests read `content = open_player()` the way they used to read
    `content = _open_player(page, live_server)`.
    """

    def _open(content=None, viewport=None):
        content = content or demo_content
        # Before navigating: a resize after load would re-run the geometry under test.
        if viewport:
            logged_in_page.set_viewport_size(viewport)
        logged_in_page.goto(f"{live_server.url}/player/{content.pk}/")
        logged_in_page.wait_for_function(
            _PLAYER_READY, arg=VIDEO_SELECTOR, timeout=READY_TIMEOUT_MS
        )
        return content

    return _open


@pytest.fixture
def open_editor(logged_in_page, live_server, demo_content):
    """Open the video editor on a content and wait until its timeline is drawn."""

    def _open(content=None):
        content = content or demo_content
        logged_in_page.goto(f"{live_server.url}/video-editor/{content.pk}/")
        logged_in_page.wait_for_function(
            _EDITOR_READY, arg=VIDEO_SELECTOR, timeout=READY_TIMEOUT_MS
        )
        return content

    return _open
