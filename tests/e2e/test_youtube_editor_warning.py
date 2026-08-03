from playwright.sync_api import expect
import pytest

from core.models import Content
from core.models import Playlist
from core.youtube import get_or_create_youtube_resource

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.django_db(transaction=True),
]


def _create_youtube_content(title):
    playlist = Playlist.objects.get(name="Local Admin / Demo Review Shelf")
    resource = get_or_create_youtube_resource("eHEsJyVQn3w", playlist.owner.username)
    return Content.objects.create(
        playlist=playlist,
        title=title,
        url="https://www.youtube.com/watch?v=eHEsJyVQn3w",
        resource=resource,
    )


def test_editing_youtube_content_shows_warning_banner(
    page, live_server, seeded_demo_data
):
    content = _create_youtube_content("Editor Warning - Shows Banner")

    page.goto(f"{live_server.url}/login/dev/quick/")
    page.goto(f"{live_server.url}/video-editor/{content.pk}/")

    banner = page.locator("#youtube-editor-warning-banner")
    expect(banner).to_be_visible()
    expect(banner).to_contain_text("at your own risk")


def test_warning_banner_has_no_dismiss_control(page, live_server, seeded_demo_data):
    content = _create_youtube_content("Editor Warning - Not Dismissable")

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
