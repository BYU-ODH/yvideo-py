"""A zoomed timeline stays where it was put when the window changes size.

The timeline is `100 * zoomLevel` percent wide, so its pixel width follows the window - but the
browser stores a scroll position in pixels. Resizing the window therefore left the same pixel
offset pointing at a different time, sliding the tracks out from under whatever the user was
working on (and, when the window grew, snapping them back toward the start). The scroll position is
now remembered as a fraction of the timeline, which is what stays meaningful across a resize.

Nothing here is specific to YouTube or to long videos - it needs only a zoom level above 1, which
is why it runs on the seeded file-backed content like the rest of the editor tests.
"""

import pytest

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.django_db(transaction=True),
]

# 1.5x per click: enough that a good deal of the timeline is off-screen and there is somewhere to
# scroll to.
ZOOM_CLICKS = 4
# Far enough in that a drift in either direction is unambiguous, and not so far that the scroll
# lands against the end stop where clamping would mask it.
SCROLL_TO_PERCENT = 30

WIDE_VIEWPORT = {"width": 1400, "height": 900}
NARROW_VIEWPORT = {"width": 900, "height": 900}


def _timeline_state(page):
    """Where the visible window starts, in both units that matter."""
    return page.evaluate(
        """() => {
            const content = document.getElementById('timeline-ticks-content');
            const total = document.getElementById('tick-marks-container');
            const duration = document.querySelector('#video-player').duration;
            const contentWidth = total.getBoundingClientRect().width;
            return {
                scrollLeft: content.scrollLeft,
                contentWidth: contentWidth,
                leftEdgeSeconds: duration * (content.scrollLeft / contentWidth),
                trackScrollLefts: [...document.getElementsByClassName('timeline-track-row-right')]
                    .map((row) => row.scrollLeft),
            };
        }"""
    )


def _zoom_and_scroll(page):
    for _ in range(ZOOM_CLICKS):
        page.locator("#zoom-in-button").click()
    # Driven through the slider's own input event, the way a user scrolls the timeline.
    page.evaluate(
        """(percent) => {
            const slider = document.getElementById('timeline-scroll-input');
            slider.value = percent;
            slider.dispatchEvent(new Event('input', {bubbles: true}));
        }""",
        SCROLL_TO_PERCENT,
    )
    page.wait_for_timeout(200)


def test_narrowing_the_window_keeps_the_same_time_on_screen(page, open_editor):
    page.set_viewport_size(WIDE_VIEWPORT)
    open_editor()
    _zoom_and_scroll(page)

    before = _timeline_state(page)
    assert before["scrollLeft"] > 0, (
        "the timeline did not scroll, so this proves nothing"
    )

    page.set_viewport_size(NARROW_VIEWPORT)
    page.wait_for_timeout(300)
    after = _timeline_state(page)

    assert after["contentWidth"] < before["contentWidth"] - 50, (
        "the timeline's pixel width did not change with the window, so a pixel-based scroll "
        "position would not have drifted and this test would pass vacuously"
    )
    assert after["leftEdgeSeconds"] == pytest.approx(
        before["leftEdgeSeconds"], abs=0.2
    ), (
        f"the visible window moved from {before['leftEdgeSeconds']:.2f}s to "
        f"{after['leftEdgeSeconds']:.2f}s when the window was resized"
    )


def test_widening_the_window_keeps_the_same_time_on_screen(page, open_editor):
    # The other direction, where the drift ran backwards: a wider window made the timeline wider
    # in pixels, so the unchanged scroll offset pointed at an earlier time.
    page.set_viewport_size(NARROW_VIEWPORT)
    open_editor()
    _zoom_and_scroll(page)

    before = _timeline_state(page)
    assert before["scrollLeft"] > 0, (
        "the timeline did not scroll, so this proves nothing"
    )

    page.set_viewport_size(WIDE_VIEWPORT)
    page.wait_for_timeout(300)
    after = _timeline_state(page)

    assert after["contentWidth"] > before["contentWidth"] + 50
    assert after["leftEdgeSeconds"] == pytest.approx(
        before["leftEdgeSeconds"], abs=0.2
    ), (
        f"the visible window moved from {before['leftEdgeSeconds']:.2f}s to "
        f"{after['leftEdgeSeconds']:.2f}s when the window was resized"
    )


def test_the_tracks_and_the_tick_strip_stay_in_step_across_a_resize(page, open_editor):
    # They are separate scroll containers kept in sync by scrollTracksToPoint. A resize that
    # repositioned only the ruler would leave items sitting under the wrong times.
    page.set_viewport_size(WIDE_VIEWPORT)
    open_editor()
    _zoom_and_scroll(page)

    page.set_viewport_size(NARROW_VIEWPORT)
    page.wait_for_timeout(300)
    after = _timeline_state(page)

    assert after["trackScrollLefts"], "no track rows were found"
    for offset in after["trackScrollLefts"]:
        assert offset == pytest.approx(after["scrollLeft"], abs=1)


def test_the_scroll_slider_reflects_where_the_timeline_actually_is(page, open_editor):
    # Zooming scrolls the timeline to centre the playhead without going near the slider, which
    # used to leave the thumb showing a position the timeline had left behind - so the next drag
    # of it jumped somewhere unrelated.
    page.set_viewport_size(WIDE_VIEWPORT)
    open_editor()
    page.evaluate("() => window.videoPlayer.setCurrentTime(20)")
    page.wait_for_timeout(200)
    for _ in range(ZOOM_CLICKS):
        page.locator("#zoom-in-button").click()
    page.wait_for_timeout(200)

    state = _timeline_state(page)
    slider_percent = page.evaluate(
        "() => parseFloat(document.getElementById('timeline-scroll-input').value)"
    )

    assert state["scrollLeft"] > 0, (
        "zooming did not scroll the timeline, so this proves nothing"
    )
    assert slider_percent == pytest.approx(
        state["scrollLeft"] / state["contentWidth"] * 100, abs=1
    )
