"""Hovering or dragging the scrubber never moves or resizes the video or its annotation overlay (#598).

In the editor the controls sit below the video in normal flow, so any change in the scrubber's
height shrinks the video. Every frame is sampled through the scrubber's grow/shrink transition,
because a mismatch that only lasts while the transition runs is still visible to the user.
"""

from playwright.sync_api import expect
import pytest

from tests.e2e.conftest import VIDEO_SELECTOR

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.django_db(transaction=True),
]

# Longer than the scrubber's 0.2s transition.
SETTLE_MS = 400

_START_SAMPLING = """(videoSelector) => {
    const rect = (sel) => {
        const r = document.querySelector(sel).getBoundingClientRect();
        return {x: r.x, y: r.y, width: r.width, height: r.height};
    };
    window.__frames = [];
    window.__sampling = true;
    const sample = () => {
        if (!window.__sampling) return;
        window.__frames.push({
            video: rect(videoSelector),
            overlay: rect('.annotation-box'),
            bar: document.querySelector('.scrubber').clientHeight,
        });
        requestAnimationFrame(sample);
    };
    requestAnimationFrame(sample);
}"""

# Where the picture sits inside the video element (object-fit: contain), derived independently of
# the app's own contentRect().
_PICTURE_RECT = """(sel) => {
    const el = document.querySelector(sel);
    const box = el.getBoundingClientRect();
    const scale = Math.min(box.width / el.videoWidth, box.height / el.videoHeight);
    const width = el.videoWidth * scale;
    const height = el.videoHeight * scale;
    return {
        x: box.x + (box.width - width) / 2,
        y: box.y + (box.height - height) / 2,
        width,
        height,
    };
}"""


def _stop_sampling(page):
    return page.evaluate("() => { window.__sampling = false; return window.__frames; }")


def _assert_same_rect(a, b, tol=0.5):
    for key in ("x", "y", "width", "height"):
        assert abs(a[key] - b[key]) <= tol, (key, a, b)


def _exercise_scrubber(page):
    """Hover, unhover, then drag the scrubber, sampling layout every frame."""
    scrubber = page.locator(".annotation-player-container .scrubber")
    expect(scrubber).to_be_visible()
    box = scrubber.bounding_box()
    center_y = box["y"] + box["height"] / 2

    # Park the mouse on the video (so the controls stay shown) but off the scrubber.
    video_box = page.locator(VIDEO_SELECTOR).bounding_box()
    page.mouse.move(video_box["x"] + video_box["width"] / 2, video_box["y"] + 20)
    page.wait_for_timeout(SETTLE_MS)

    page.evaluate(_START_SAMPLING, VIDEO_SELECTOR)

    page.mouse.move(box["x"] + box["width"] * 0.3, center_y)
    page.wait_for_timeout(SETTLE_MS)
    page.mouse.move(video_box["x"] + video_box["width"] / 2, video_box["y"] + 20)
    page.wait_for_timeout(SETTLE_MS)

    page.mouse.move(box["x"] + box["width"] * 0.3, center_y)
    page.mouse.down()
    # Drag up off the bar: it stays expanded while dragging, and must still not reflow.
    page.mouse.move(box["x"] + box["width"] * 0.5, center_y - 40, steps=5)
    page.wait_for_timeout(SETTLE_MS)
    assert scrubber.evaluate("el => el.clientHeight") == 6
    page.mouse.up()
    page.wait_for_timeout(SETTLE_MS)

    return _stop_sampling(page)


def _assert_layout_never_moves(page):
    frames = _exercise_scrubber(page)

    # The bar visibly grew and shrank, so the hover and drag really happened.
    bar_heights = {f["bar"] for f in frames}
    assert {4, 6} <= bar_heights, bar_heights

    first = frames[0]
    for frame in frames:
        assert frame["video"] == first["video"]
        assert frame["overlay"] == first["overlay"]

    _assert_same_rect(first["overlay"], page.evaluate(_PICTURE_RECT, VIDEO_SELECTOR))


def test_scrubber_hover_and_drag_do_not_move_video_in_editor(
    open_editor, logged_in_page
):
    page = logged_in_page
    page.set_viewport_size({"width": 1280, "height": 900})
    open_editor()

    _assert_layout_never_moves(page)


def test_scrubber_hover_and_drag_do_not_move_video_in_player(
    open_player, logged_in_page
):
    open_player(viewport={"width": 1280, "height": 900})

    _assert_layout_never_moves(logged_in_page)
