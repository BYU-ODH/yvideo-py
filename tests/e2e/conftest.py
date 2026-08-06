from io import StringIO
import os

from django.core.management import call_command
import pytest

# pytest-playwright initializes an event loop before Django's test DB setup.
# These browser tests intentionally use Django's sync ORM and live server.
os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")

# Seeded by core/dev_seed.py, and the subject of nearly every test here: it carries the real
# birds.mp4, one clip, and the seeded blurs.
DEMO_CONTENT_TITLE = "Birds Overview"

VIDEO_SELECTOR = ".annotation-player-container video"

# Generous on purpose. This is a cold page load in a real browser - video metadata over the live
# server, a player-data fetch, and on the editor a timeline render - and a tight ceiling here buys
# nothing: a shorter timeout does not make a passing test faster, it only makes a slow machine
# report a flake instead of a failure. The files these fixtures replaced had drifted to four
# different values (1s to 10s) for the same wait.
READY_TIMEOUT_MS = 10_000

# window.videoPlayer and the video's duration load via two independent async paths (a player-data
# fetch vs. the browser's own media loading) with no ordering guarantee between them. Waiting on
# duration alone races window.videoPlayer.loadData, which several tests call immediately -
# fast/warm machines usually win that race, CI does not.
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
