from playwright.sync_api import expect
import pytest

from core.models import Content

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.django_db(transaction=True),
]


def _spoof_as(page, live_server, search_text):
    # Log in as the admin (the only account dev-quick-login can authenticate
    # as), then drive the real "Act for" UI to start spoofing another user -
    # exactly how this happens in production. This also reproduces the exact
    # condition behind the regression below: header.html only renders the
    # CSRF-token-bearing spoof form for admins who are *not* currently
    # spoofing, so once spoofing starts, that input disappears from the page.
    page.goto(f"{live_server.url}/login/dev/quick/")
    page.goto(f"{live_server.url}/")

    page.locator("#spoof-user-input").fill(search_text)
    option = page.locator("#spoof-user-select option[value]").first
    option.wait_for(state="attached", timeout=3000)
    page.locator("#spoof-user-select").select_option(
        value=option.get_attribute("value")
    )
    page.locator("#spoof-user-submit").click()


def _assert_video_plays(page, console_errors):
    video = page.locator(".annotation-player-container video")
    expect(video).to_be_visible()

    page.wait_for_function(
        """() => {
            const video = document.querySelector('.annotation-player-container video');
            return video && !isNaN(video.duration) && video.duration > 0;
        }""",
        timeout=5000,
    )

    advanced = page.evaluate(
        """async () => {
            const video = document.querySelector('.annotation-player-container video');
            video.muted = true;
            const startTime = video.currentTime;
            await video.play();
            await new Promise((resolve) => setTimeout(resolve, 500));
            video.pause();
            return video.currentTime - startTime;
        }"""
    )

    assert advanced > 0, "video did not advance during playback"
    assert not console_errors, f"unexpected page errors: {console_errors}"


def test_video_can_be_played_on_the_player_page(page, live_server, seeded_demo_data):
    console_errors = []
    page.on("pageerror", lambda exc: console_errors.append(str(exc)))

    content = Content.objects.get(title="Birds Overview")

    page.goto(f"{live_server.url}/login/dev/quick/")
    page.goto(f"{live_server.url}/player/{content.pk}/")

    _assert_video_plays(page, console_errors)


def test_video_can_be_played_by_a_spoofed_non_admin_user(
    page, live_server, seeded_demo_data
):
    # Regression coverage: player-wrapper.js used to fetch the CSRF token
    # from a `[name=csrfmiddlewaretoken]` form input that only exists on the
    # page for admins who aren't spoofing (see header.html's "Act for" form).
    # Any other viewer - a spoofed user, or a regular student/instructor in
    # production - had no such input anywhere on the page, so
    # `document.querySelector(...).value` threw and the player never
    # initialized. Reproduce that exact viewer state via real spoofing.
    console_errors = []
    page.on("pageerror", lambda exc: console_errors.append(str(exc)))

    content = Content.objects.get(title="Birds Overview")

    _spoof_as(page, live_server, "Alice")
    page.goto(f"{live_server.url}/player/{content.pk}/")

    assert not page.locator("[name=csrfmiddlewaretoken]").count(), (
        "test no longer reproduces the regression: a csrfmiddlewaretoken "
        "input is present on the page for the spoofed user"
    )

    _assert_video_plays(page, console_errors)
