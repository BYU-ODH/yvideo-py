"""The two tests here that assert what only the real YouTube IFrame Player API can answer.

Every other YouTube test runs against tests/e2e/fake_youtube.py, because what they assert is our
side of the boundary. These two assert the boundary itself, which a fake cannot: that the real API
is asked for an embed with its own UI turned off, and that the iframe it injects - sized in pixels
by the API, independent of the element's box - is forced to fill that box by our CSS. The overlay
geometry in test_youtube_video_sizing.py rests on that last part being true.

Whether the fake still *behaves* like the real API - which is the other reason to keep talking to
it - is test_youtube_api_contract.py's job.

Consequently this file needs network access and needs one specific third-party video to still
exist. Neither is a claim about this repo, so when either is missing these skip rather than fail;
the `live_youtube` fixture reports which.
"""

from urllib.parse import parse_qs
from urllib.parse import urlparse

import pytest

from core.models import Content
from core.models import Playlist
from core.youtube_utils import get_or_create_youtube_resource

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.live_youtube,
    pytest.mark.django_db(transaction=True),
]


@pytest.fixture
def live_youtube_editor(
    live_youtube, require_live_embed, page, live_server, seeded_demo_data
):
    playlist = Playlist.objects.get(name="Demo Review Shelf")
    resource = get_or_create_youtube_resource(live_youtube, playlist.owner.username)
    content = Content.objects.create(
        playlist=playlist,
        title="Live YouTube Embed",
        url=f"https://www.youtube.com/watch?v={live_youtube}",
        resource=resource,
    )

    page.goto(f"{live_server.url}/login/dev/quick/")
    page.goto(f"{live_server.url}/video-editor/{content.pk}/")
    # Not a plain wait for the iframe: where the embed is refused, YouTubeVideoElement replaces the
    # iframe with its error notice, and a bare selector wait would report a timeout that reads like
    # a bug here rather than a network that will not play YouTube videos.
    require_live_embed()
    return content


def test_youtube_player_disables_native_controls(live_youtube_editor, page):
    iframe_src = page.eval_on_selector("youtube-video iframe", "el => el.src")

    # The YouTube IFrame Player API takes these as query params on the
    # generated embed URL - asserting on the URL (rather than reaching into
    # the cross-origin iframe's rendered DOM, which Playwright/browsers can't
    # do) is what confirms AnnotationPlayer.js's own controls are the only
    # playback UI we're asking YouTube to render.
    query = parse_qs(urlparse(iframe_src).query)
    assert query.get("controls") == ["0"]
    assert query.get("disablekb") == ["1"]


def test_the_real_embed_fills_the_element_box(live_youtube_editor, page):
    # The API sizes its iframe in pixels of its own choosing; `.annotation-player-container
    # youtube-video > *` overrides that to fill the element box. If it ever stopped working, the
    # annotation overlay would be computed from a box the picture no longer occupies - and the
    # faked tests could not tell, because the fake builds its own iframe.
    # The fixture already waited for a loaded, un-refused embed; the duration is what it waited on.
    boxes = page.evaluate(
        """() => {
            const element = document.querySelector('youtube-video');
            const iframe = element.querySelector('iframe');
            return {
                element: element.getBoundingClientRect().toJSON(),
                iframe: iframe.getBoundingClientRect().toJSON(),
            };
        }"""
    )

    assert boxes["element"]["width"] > 0 and boxes["element"]["height"] > 0
    for key in ("x", "y", "width", "height"):
        assert boxes["iframe"][key] == pytest.approx(boxes["element"][key], abs=1)
