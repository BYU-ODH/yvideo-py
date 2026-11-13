"""The IC controller driving a separate output window."""

from playwright.sync_api import expect
import pytest

from ic.testing import FFMPEG_AVAILABLE

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.django_db(transaction=True),
    pytest.mark.skipif(
        not FFMPEG_AVAILABLE, reason="needs ffmpeg to make playable videos"
    ),
]

OUTPUT_TIME = "() => window.icOutputPlayer.getCurrentTime()"


def open_output(page):
    with page.expect_popup() as popup_info:
        page.locator("#ic-open-output").click()
    output = popup_info.value
    output.wait_for_load_state()
    expect(page.locator("#ic-output-status")).to_contain_text(
        "Output connected", timeout=5000
    )
    return output


def test_controller_drives_the_output_window(logged_in_page, live_server, ic_program):
    page = logged_in_page
    page.goto(f"{live_server.url}/ic/program/{ic_program['program'].pk}/")
    expect(page.locator("#ic-now-title")).to_have_text("Nothing loaded")
    output = open_output(page)

    # The output shows the IC lockup, and nothing else, until something plays.
    expect(output.locator("#ic-idle")).to_have_class("ic-idle ic-visible")
    expect(output.locator(".video-controls")).to_be_hidden()

    # Space starts the first item: the looping announcement.
    page.bring_to_front()
    page.locator("body").click()
    page.keyboard.press(" ")
    expect(page.locator("#ic-now-title")).to_have_text("Pre-show announcements")
    output.wait_for_function(
        "() => !document.getElementById('video-player').paused", timeout=8000
    )
    assert output.evaluate("() => document.getElementById('video-player').loop") is True
    # The operator's set-up hint never shows over a film.
    expect(output.locator("#ic-fullscreen-hint")).to_be_hidden()

    # N advances to the feature, which loads its annotations and its VTT track.
    page.keyboard.press("n")
    expect(page.locator("#ic-now-title")).to_have_text("Amélie")
    output.wait_for_function(
        "() => window.icOutputPlayer.annotations.length > 0 && !document.getElementById('video-player').paused",
        timeout=8000,
    )
    assert (
        output.evaluate("() => document.getElementById('video-player').loop") is False
    )
    assert (
        output.evaluate(
            "() => document.getElementById('video-player').textTracks.length"
        )
        == 1
    )

    # The skip (1 s → 4 s) is honoured, exactly as in the normal player.
    output.evaluate(
        "() => { const v = document.getElementById('video-player'); v.muted = true; }"
    )
    page.evaluate("() => window.icControl.send('seek', { time: 0.8 })")
    output.wait_for_function(f"() => ({OUTPUT_TIME})() >= 3.9", timeout=5000)

    # The controller shows what the output reports.
    expect(page.locator("#ic-time")).not_to_have_text("0:00:00", timeout=3000)

    # Emergency blank covers the projector immediately.
    page.keyboard.press("b")
    expect(output.locator("#ic-blank")).to_be_visible()
    expect(page.locator("#ic-blank")).to_have_attribute("aria-pressed", "true")
    page.keyboard.press("b")
    expect(output.locator("#ic-blank")).to_be_hidden()


def test_controller_notices_a_lost_output_and_can_resume(
    logged_in_page, live_server, ic_program
):
    page = logged_in_page
    page.goto(f"{live_server.url}/ic/program/{ic_program['program'].pk}/")
    output = open_output(page)
    page.locator("[data-ic-load='1']").click()
    page.locator("#ic-play-pause").click()
    output.wait_for_function(
        "() => !document.getElementById('video-player').paused", timeout=8000
    )
    output.close()
    expect(page.locator("#ic-output-lost")).to_be_visible(timeout=5000)

    with page.expect_popup() as popup_info:
        page.locator("#ic-reopen-output").click()
    reopened = popup_info.value
    reopened.wait_for_load_state()
    reopened.wait_for_function(
        "() => window.icOutputPlayer && document.getElementById('video-player').currentSrc.includes('/media/')",
        timeout=8000,
    )
    expect(page.locator("#ic-output-lost")).to_be_hidden(timeout=5000)
