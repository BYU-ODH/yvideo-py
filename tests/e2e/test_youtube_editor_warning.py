"""The editor's YouTube warning, and what it is allowed to be.

Run against the fake IFrame Player API: the banner is server-rendered markup that has nothing to
do with the player, so reaching youtube.com here would only add a way for these to fail.
"""

from playwright.sync_api import expect
import pytest

from core.models import Content

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.django_db(transaction=True),
]


def test_editing_youtube_content_shows_warning_banner(
    fake_youtube, live_server, youtube_content, page
):
    content = youtube_content("Editor Warning - Shows Banner")

    page.goto(f"{live_server.url}/login/dev/quick/")
    page.goto(f"{live_server.url}/video-editor/{content.pk}/")

    banner = page.locator("#youtube-editor-warning-banner")
    expect(banner).to_be_visible()
    expect(banner).to_contain_text("at your own risk")
    # The part instructors cannot discover from the editor itself: YouTube draws its own links
    # over the video and no annotation can cover them. See MANUAL_TESTING.md item 7.
    expect(banner).to_contain_text("More videos")


def test_warning_banner_has_no_dismiss_control(
    fake_youtube, live_server, youtube_content, page
):
    content = youtube_content("Editor Warning - Not Dismissable")

    page.goto(f"{live_server.url}/login/dev/quick/")
    page.goto(f"{live_server.url}/video-editor/{content.pk}/")

    banner = page.locator("#youtube-editor-warning-banner")
    expect(banner).to_be_visible()
    assert banner.locator("button, a").count() == 0


def test_file_backed_editor_does_not_show_youtube_warning(
    page, live_server, seeded_demo_data
):
    content = Content.objects.get(title="Birds Overview")

    page.goto(f"{live_server.url}/login/dev/quick/")
    page.goto(f"{live_server.url}/video-editor/{content.pk}/")

    expect(page.locator("#youtube-editor-warning-banner")).to_have_count(0)


def test_youtube_editor_offers_no_subtitle_editor(
    fake_youtube, live_server, youtube_content, page
):
    # A YouTube embed exposes no TextTrack surface, so subtitles edited here could never be
    # displayed - offering the panel would invite work that is silently discarded. Both the panel
    # and the buttons that switch to it are absent (see annotation_panel.html).
    content = youtube_content("Editor Warning - No Subtitle Panel")

    page.goto(f"{live_server.url}/login/dev/quick/")
    page.goto(f"{live_server.url}/video-editor/{content.pk}/")
    page.wait_for_selector("#editor-annotation-panel")

    expect(page.locator("#subtitle-editor-panel")).to_have_count(0)
    expect(page.locator("#annotation-panel-switch")).to_have_count(0)
    expect(page.locator("#subtitle-panel-switch")).to_have_count(0)


def test_file_backed_editor_still_offers_the_subtitle_editor(
    page, live_server, seeded_demo_data
):
    # The other half of the test above: the panel is only withheld from YouTube content, and this
    # would catch a guard that hid it from everyone.
    content = Content.objects.get(title="Birds Overview")

    page.goto(f"{live_server.url}/login/dev/quick/")
    page.goto(f"{live_server.url}/video-editor/{content.pk}/")
    page.wait_for_selector("#editor-annotation-panel")

    expect(page.locator("#subtitle-editor-panel")).to_have_count(1)
    expect(page.locator("#annotation-panel-switch")).to_have_count(1)
