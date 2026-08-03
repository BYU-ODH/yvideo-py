from urllib.parse import parse_qs
from urllib.parse import urlparse

import pytest

from core.models import Content
from core.models import Playlist
from core.youtube import get_or_create_youtube_resource

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.django_db(transaction=True),
]


def test_youtube_player_disables_native_controls(page, live_server, seeded_demo_data):
    playlist = Playlist.objects.get(name="Local Admin / Demo Review Shelf")
    resource = get_or_create_youtube_resource("eHEsJyVQn3w", playlist.owner.username)
    content = Content.objects.create(
        playlist=playlist,
        title="Controls - Disabled",
        url="https://www.youtube.com/watch?v=eHEsJyVQn3w",
        resource=resource,
    )

    page.goto(f"{live_server.url}/login/dev/quick/")
    page.goto(f"{live_server.url}/video-editor/{content.pk}/")

    page.wait_for_selector("youtube-video iframe", timeout=15000)
    iframe_src = page.eval_on_selector("youtube-video iframe", "el => el.src")

    # The YouTube IFrame Player API takes these as query params on the
    # generated embed URL - asserting on the URL (rather than reaching into
    # the cross-origin iframe's rendered DOM, which Playwright/browsers can't
    # do) is what confirms AnnotationPlayer.js's own controls are the only
    # playback UI we're asking YouTube to render.
    query = parse_qs(urlparse(iframe_src).query)
    assert query.get("controls") == ["0"]
    assert query.get("disablekb") == ["1"]
