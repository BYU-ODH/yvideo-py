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
            // window.videoPlayer and the video's duration load via two
            // independent async paths (a player-data fetch vs. the browser's
            // own media loading) with no ordering guarantee between them.
            // Waiting on duration alone races window.videoPlayer.loadData
            // below - fast/warm machines usually win the race, CI doesn't.
            return window.videoPlayer && video && !isNaN(video.duration) && video.duration > 0;
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


def _video_and_annotation_box_rects(page):
    return page.evaluate(
        """() => {
            const video = document.querySelector('.annotation-player-container video');
            const box = document.querySelector('.annotation-box');
            return {video: video.getBoundingClientRect(), box: box.getBoundingClientRect()};
        }"""
    )


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


def test_video_is_pillarboxed_and_blur_stays_aligned_in_fullscreen(
    page, live_server, seeded_demo_data
):
    # birds.mp4 is 16:9. A viewport proportionally *wider* than that (here
    # roughly 2.67:1) is the mirror image of the other fullscreen test: the
    # container is now wider than the video needs, so height - not width -
    # is the constraining dimension, and .video-wrapper must switch into its
    # "full-height" branch (width: auto) to pillarbox on the sides instead of
    # stretching to the container's full width. Nothing else in this file
    # exercises that branch.
    _open_player(page, live_server, viewport={"width": 1600, "height": 600})
    _inject_blur_annotation(page)

    before = _blur_position_relative_to_video(page)

    page.locator(".fullscreen-btn").click()
    page.wait_for_function("() => !!document.fullscreenElement", timeout=2000)
    page.wait_for_timeout(300)

    has_full_height = page.evaluate(
        "() => document.querySelector('.video-wrapper').classList.contains('full-height')"
    )
    assert has_full_height, (
        "expected the height-constrained branch to engage for a container "
        "this much wider than the video's own aspect ratio"
    )

    video_box = page.evaluate(
        """() => document.querySelector('.annotation-player-container video')
            .getBoundingClientRect()"""
    )
    viewport = page.viewport_size

    # Height fills the screen; width is narrower than the screen (proving
    # this didn't just vacuously stretch edge-to-edge) and centered within it.
    assert video_box["height"] == pytest.approx(viewport["height"], abs=2)
    assert video_box["width"] < viewport["width"] - 2
    expected_x = (viewport["width"] - video_box["width"]) / 2
    assert video_box["x"] == pytest.approx(expected_x, abs=2)

    rects = _video_and_annotation_box_rects(page)
    assert rects["box"]["x"] == pytest.approx(rects["video"]["x"], abs=1)
    assert rects["box"]["y"] == pytest.approx(rects["video"]["y"], abs=1)
    assert rects["box"]["width"] == pytest.approx(rects["video"]["width"], abs=1)
    assert rects["box"]["height"] == pytest.approx(rects["video"]["height"], abs=1)

    after = _blur_position_relative_to_video(page)
    _assert_relative_positions_match(before, after)


def test_video_is_vertically_centered_and_blur_stays_aligned_in_portrait_fullscreen(
    page, live_server, seeded_demo_data
):
    # A viewport much narrower than the video's own 16:9 - e.g. a phone held
    # upright - is width-constrained, so the video letterboxes top/bottom
    # instead of filling the screen's height. The video block must be
    # centered in that leftover vertical space, not pinned to the top edge,
    # and the blur/annotation container must track the video exactly
    # regardless of where it ends up on screen.
    _open_player(page, live_server, viewport={"width": 300, "height": 1600})
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

    assert video_box["width"] == pytest.approx(viewport["width"], abs=2)
    assert video_box["height"] < viewport["height"] - 2
    expected_y = (viewport["height"] - video_box["height"]) / 2
    assert video_box["y"] == pytest.approx(expected_y, abs=2), (
        "video should be vertically centered in the letterboxed space, not "
        "pinned to the top of the screen"
    )

    rects = _video_and_annotation_box_rects(page)
    assert rects["box"]["x"] == pytest.approx(rects["video"]["x"], abs=1)
    assert rects["box"]["y"] == pytest.approx(rects["video"]["y"], abs=1)
    assert rects["box"]["width"] == pytest.approx(rects["video"]["width"], abs=1)
    assert rects["box"]["height"] == pytest.approx(rects["video"]["height"], abs=1)

    after = _blur_position_relative_to_video(page)
    _assert_relative_positions_match(before, after)


def test_blur_stays_aligned_after_exiting_fullscreen(
    page, live_server, seeded_demo_data
):
    # Fullscreen escapes the normal page flow (position: fixed, top: 0), so
    # the video's on-screen y-offset reliably differs from windowed mode
    # (which sits below the page header) regardless of viewport size - a
    # simple, non-vacuous signal that fullscreen actually engaged. Exiting
    # must put the layout back the way it was, not just drop the
    # :fullscreen styling while some other stale sizing lingers, and the
    # blur must still track the video correctly once back in the normal
    # page flow.
    _open_player(page, live_server, viewport={"width": 1280, "height": 720})
    _inject_blur_annotation(page)

    before = _blur_position_relative_to_video(page)
    video_box_before = page.evaluate(
        "() => document.querySelector('.annotation-player-container video')"
        ".getBoundingClientRect()"
    )

    page.locator(".fullscreen-btn").click()
    page.wait_for_function("() => !!document.fullscreenElement", timeout=2000)
    page.wait_for_timeout(300)

    video_box_fullscreen = page.evaluate(
        "() => document.querySelector('.annotation-player-container video')"
        ".getBoundingClientRect()"
    )
    assert video_box_fullscreen["y"] != video_box_before["y"], (
        "video's position did not actually change on entering fullscreen - "
        "this test would pass vacuously without a real layout change to revert"
    )

    page.evaluate("() => document.exitFullscreen()")
    page.wait_for_function("() => !document.fullscreenElement", timeout=2000)
    page.wait_for_timeout(300)

    video_box_after = page.evaluate(
        "() => document.querySelector('.annotation-player-container video')"
        ".getBoundingClientRect()"
    )
    assert video_box_after["y"] == pytest.approx(video_box_before["y"], abs=2), (
        "video did not revert to its windowed position after exiting fullscreen"
    )
    assert video_box_after["width"] == pytest.approx(video_box_before["width"], abs=2)
    assert video_box_after["height"] == pytest.approx(video_box_before["height"], abs=2)

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
