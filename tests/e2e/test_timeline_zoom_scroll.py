"""A zoomed timeline stays where it was put, with every track lined up under the ruler.

The timeline is `100 * zoomLevel` percent wide, so its pixel width follows the window - but the
browser stores a scroll position in pixels. The scroll position is therefore remembered as a
fraction of the timeline, which is what stays meaningful across a resize.

The ruler and each track are separate scroll containers, and the browser scrolls whichever one is
under the pointer. Whichever row scrolls, the others follow it, a track can never scroll past the
end of the ruler, and tracks rebuilt from server HTML start at the current zoom and scroll
position. The alignment tests compare on-screen rectangles, so a track at the wrong width and a
track at the wrong offset both show up as misaligned.

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


def _misalignments(page):
    """Every track whose content does not sit exactly under the ruler's, in pixels on screen.

    Comparing on-screen rectangles covers width and scroll at once: a track zoomed to a different
    width or scrolled to a different offset puts its content somewhere the ruler's is not.
    """
    return page.evaluate(
        """() => {
            const ruler = document.getElementById('tick-marks-container').getBoundingClientRect();
            return [...document.querySelectorAll('.track-row-annotations-container')]
                .map((container) => container.getBoundingClientRect())
                .filter((track) => Math.abs(track.left - ruler.left) > 1
                    || Math.abs(track.width - ruler.width) > 1)
                .map((track) => ({left: track.left, width: track.width,
                                  rulerLeft: ruler.left, rulerWidth: ruler.width}));
        }"""
    )


def test_scrolling_one_track_by_itself_carries_the_ruler_and_the_other_tracks(
    page, open_editor
):
    # A trackpad swipe or shift+wheel over a single row scrolls only that row.
    page.set_viewport_size(WIDE_VIEWPORT)
    open_editor()
    _zoom_and_scroll(page)

    page.evaluate(
        """() => {
            const row = document.getElementsByClassName('timeline-track-row-right')[0];
            row.scrollLeft = row.scrollLeft + 150;
        }"""
    )
    page.wait_for_timeout(100)

    assert _misalignments(page) == []


def test_scrolling_the_ruler_by_itself_carries_the_tracks(page, open_editor):
    # A trackpad swipe or shift+wheel over the ruler scrolls only the ruler.
    page.set_viewport_size(WIDE_VIEWPORT)
    open_editor()
    _zoom_and_scroll(page)

    page.evaluate(
        "() => { document.getElementById('timeline-ticks-content').scrollLeft -= 150; }"
    )
    page.wait_for_timeout(100)

    assert _misalignments(page) == []


def test_a_track_cannot_scroll_past_the_end_of_the_ruler(page, open_editor):
    # An item overhanging the end of its track makes that row scrollable further than the ruler.
    page.set_viewport_size(WIDE_VIEWPORT)
    open_editor()
    _zoom_and_scroll(page)

    page.evaluate(
        """() => {
            const container = document.querySelector('.track-row-annotations-container');
            const overhang = document.createElement('div');
            overhang.style.cssText = 'position: absolute; left: 100%; width: 400px; height: 1px;';
            container.appendChild(overhang);
            container.parentElement.scrollLeft = container.parentElement.scrollWidth;
        }"""
    )
    page.wait_for_timeout(100)

    assert _misalignments(page) == []


def test_a_track_added_while_zoomed_lines_up_with_the_ruler(page, open_editor):
    # Adding a track rebuilds every row from server HTML, which knows nothing of the zoom or the
    # scroll position.
    page.set_viewport_size(WIDE_VIEWPORT)
    open_editor()
    _zoom_and_scroll(page)
    track_count = page.locator(".track-row").count()

    page.locator("#timeline-new-track-button").click()
    page.locator("#new-track-name").fill("Added while zoomed")
    page.locator("#new-track-save-button").click()
    page.wait_for_function(
        "(count) => document.querySelectorAll('.track-row').length > count",
        arg=track_count,
    )

    assert _timeline_state(page)["scrollLeft"] > 0
    assert _misalignments(page) == []
