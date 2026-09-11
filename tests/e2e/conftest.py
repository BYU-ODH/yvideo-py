from io import StringIO
import os

from django.core.management import call_command
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
import pytest

from tests.e2e import live_youtube as live_youtube_api

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


@pytest.fixture(scope="session")
def live_youtube():
    """The live video id, or a skip explaining why the real API cannot be reached.

    Request this from any test that talks to youtube.com, so an offline machine reports "skipped:
    youtube.com is unreachable" instead of a failure that looks like a bug here.

    Reachable is not the same as playable: see `require_live_embed`, which is the other half of this
    gate and the one a blocked network actually trips.
    """
    reason = live_youtube_api.unavailable_reason()
    if reason:
        pytest.skip(f"needs the live YouTube API: {reason}")
    return live_youtube_api.LIVE_YOUTUBE_VIDEO_ID


@pytest.fixture
def require_live_embed(page):
    """Wait until the real embed is playable here, or skip saying it is not.

    `live_youtube` asks whether youtube.com answers HTTP. This asks the only question a browser test
    cares about - does the embed load and run - which is a different question wherever YouTube
    declines to serve video to the machine asking. See live_youtube.LIVE_EMBED_REFUSED_MESSAGE.
    """

    def _require():
        try:
            page.wait_for_function(
                f"() => ({live_youtube_api.EMBED_OUTCOME_JS})() !== null",
                timeout=live_youtube_api.LIVE_EMBED_TIMEOUT_MS,
            )
        except PlaywrightTimeoutError:
            pytest.skip(
                f"{live_youtube_api.LIVE_EMBED_REFUSED_MESSAGE} "
                "(the embed never finished loading at all)"
            )
        page.wait_for_timeout(live_youtube_api.LIVE_EMBED_SETTLE_MS)
        if page.evaluate(live_youtube_api.EMBED_OUTCOME_JS) == "refused":
            pytest.skip(
                f"{live_youtube_api.LIVE_EMBED_REFUSED_MESSAGE} "
                "(the player raised an error)"
            )

    return _require


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
        playlist = Playlist.objects.get(name="Demo Review Shelf")
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
