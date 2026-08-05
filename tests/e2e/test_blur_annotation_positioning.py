import pytest

from core.models import Content

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.django_db(transaction=True),
]

# Deliberately asymmetric (not centered, not square) so a left/right or
# top/bottom axis mixup, or a uniform-vs-per-axis scaling bug, would show up
# as a mismatch instead of coincidentally cancelling out.
BLUR_POSITION = {
    "id": 1,
    "time": 0,
    "x": 12.5,
    "y": 30,
    "width": 22,
    "height": 14,
    "blur_amount": "5px",
}


def _open_player(page, live_server, viewport=None):
    if viewport:
        page.set_viewport_size(viewport)

    content = Content.objects.get(title="Birds Overview")
    page.goto(f"{live_server.url}/login/dev/quick/")
    page.goto(f"{live_server.url}/player/{content.pk}/")
    page.wait_for_function(
        """() => {
            const video = document.querySelector('.annotation-player-container video');
            return video && !isNaN(video.duration) && video.duration > 0;
        }""",
        timeout=5000,
    )
    return content


def _inject_blur_annotation(page):
    # Bypass the editor UI and load a synthetic blur annotation directly
    # through the player's public API - the geometry being tested lives
    # entirely in AnnotationPlayer/CSS, not in how the annotation was authored.
    page.evaluate(
        """(position) => {
            window.videoPlayer.loadData({
                annotations: [{
                    id: 1,
                    type: 'blur',
                    start: 0,
                    end: 999999,
                    positions: [position],
                }],
            });
            window.videoPlayer.setCurrentTime(0.1);
        }""",
        BLUR_POSITION,
    )
    page.wait_for_selector("#blur0", state="attached")


def _blur_position_relative_to_video(page):
    # Express the blur box's on-screen geometry as a fraction of the video
    # element's own on-screen box, so it can be compared across resizes
    # regardless of how large the video is currently rendered.
    return page.evaluate(
        """() => {
            const blur = document.querySelector('#blur0');
            const video = document.querySelector('.annotation-player-container video');
            const b = blur.getBoundingClientRect();
            const v = video.getBoundingClientRect();
            return {
                x: (b.left - v.left) / v.width,
                y: (b.top - v.top) / v.height,
                width: b.width / v.width,
                height: b.height / v.height,
            };
        }"""
    )


def _assert_relative_positions_match(before, after):
    assert before["x"] == pytest.approx(after["x"], abs=0.02)
    assert before["y"] == pytest.approx(after["y"], abs=0.02)
    assert before["width"] == pytest.approx(after["width"], abs=0.02)
    assert before["height"] == pytest.approx(after["height"], abs=0.02)


def test_blur_annotation_stays_aligned_when_window_shrinks_in_both_directions(
    page, live_server, seeded_demo_data
):
    _open_player(page, live_server, viewport={"width": 1280, "height": 720})
    _inject_blur_annotation(page)

    before = _blur_position_relative_to_video(page)
    video_width_before = page.evaluate(
        "() => document.querySelector('.annotation-player-container video')"
        ".getBoundingClientRect().width"
    )

    # Shrink in both dimensions at once - not just narrower, but shorter too -
    # so a bug that only recomputes on one axis (e.g. width-only) would surface.
    page.set_viewport_size({"width": 500, "height": 380})
    page.wait_for_timeout(200)

    video_width_after = page.evaluate(
        "() => document.querySelector('.annotation-player-container video')"
        ".getBoundingClientRect().width"
    )
    assert video_width_after < video_width_before, (
        "video did not actually shrink with the window - this test would "
        "pass vacuously without a real resize"
    )

    after = _blur_position_relative_to_video(page)
    _assert_relative_positions_match(before, after)


def test_video_fills_the_screen_and_blur_stays_aligned_in_fullscreen(
    page, live_server, seeded_demo_data
):
    # birds.mp4 is exactly 16:9 (1280x720). Use a 16:9 viewport *larger* than
    # that native resolution, so "fills the screen" can be asserted as an
    # exact match (no letterboxing) while also proving the video actually
    # scales up past its native pixel size - a video-wrapper with no
    # explicit width shrinks to fit a smaller viewport fine, but without a
    # width forcing it to use all available space, it caps out at the
    # video's own intrinsic size and never grows any larger than that.
    _open_player(page, live_server, viewport={"width": 2000, "height": 1125})
    _inject_blur_annotation(page)

    before = _blur_position_relative_to_video(page)

    page.locator(".fullscreen-btn").click()
    page.wait_for_function("() => !!document.fullscreenElement", timeout=2000)
    page.wait_for_timeout(300)

    video_box = page.evaluate(
        """() => document.querySelector('.annotation-player-container video')
            .getBoundingClientRect()"""
    )
    viewport = page.viewport_size

    # The video itself - not just the player container - must occupy the
    # entire screen; a black canvas with a small video floating in a corner
    # would satisfy a container-only check but not this one.
    assert video_box["width"] == pytest.approx(viewport["width"], abs=2)
    assert video_box["height"] == pytest.approx(viewport["height"], abs=2)
    assert video_box["x"] == pytest.approx(0, abs=2)
    assert video_box["y"] == pytest.approx(0, abs=2)

    after = _blur_position_relative_to_video(page)
    _assert_relative_positions_match(before, after)
