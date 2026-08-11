"""A YouTube video that cannot be played says so, in both places it has to.

The IFrame API reports an unplayable video (removed, private, embedding disabled) through onError
and then never resolves anything else - no duration, no metadata event. The player degrades on its
own, but the editor waits for a duration before it starts, so without an explicit signal it sat
behind an empty timeline indefinitely with nothing on screen to explain why.
"""

import pytest

from tests.e2e.fake_youtube import ERROR_VIDEO_ID

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.django_db(transaction=True),
]


def test_the_player_offers_a_link_out_instead_of_a_dead_frame(
    fake_youtube, live_server, youtube_content, page
):
    content = youtube_content("Unplayable - Player", video_id=ERROR_VIDEO_ID)

    page.goto(f"{live_server.url}/login/dev/quick/")
    page.goto(f"{live_server.url}/player/{content.pk}/")

    message = page.locator(".youtube-video-error")
    message.wait_for(timeout=10000)
    assert "can't be played" in message.inner_text()
    link = message.locator("a")
    assert ERROR_VIDEO_ID in link.get_attribute("href")
    # A new tab, since navigating the player page away would lose the user's place.
    assert link.get_attribute("target") == "_blank"


def test_the_editor_explains_itself_instead_of_waiting_forever(
    fake_youtube, live_server, youtube_content, page
):
    content = youtube_content("Unplayable - Editor", video_id=ERROR_VIDEO_ID)

    page.goto(f"{live_server.url}/login/dev/quick/")
    page.goto(f"{live_server.url}/video-editor/{content.pk}/")

    banner = page.locator("#editor-video-error-banner")
    banner.wait_for(timeout=10000)
    assert "cannot be played" in banner.inner_text()
    # Says what it means for work already done, because the obvious fear on seeing this is that
    # annotations went with the video.
    assert "unaffected" in banner.inner_text()


def test_a_playable_video_shows_neither_message(
    fake_youtube, live_server, youtube_content, page
):
    content = youtube_content("Unplayable - Control")

    page.goto(f"{live_server.url}/login/dev/quick/")
    page.goto(f"{live_server.url}/video-editor/{content.pk}/")
    page.wait_for_function(
        "() => { const yt = document.querySelector('youtube-video');"
        " return yt && !isNaN(yt.duration) && yt.duration > 0; }",
        timeout=15000,
    )

    assert page.locator("#editor-video-error-banner").count() == 0
    assert page.locator(".youtube-video-error").count() == 0
