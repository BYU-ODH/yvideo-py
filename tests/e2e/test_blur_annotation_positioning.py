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
    page.wait_for_selector("#blur-overlay-1", state="attached")


# The rectangle the picture actually occupies inside the video element. Since the video is
# `object-fit: contain`, its element box is generally *larger* than the picture - the leftover
# space is letterbox/pillarbox bars - so the element box is not what a blur's percentages are
# relative to.
#
# This is re-derived from videoWidth/videoHeight here rather than by calling the app's own
# contentRect(), so it stays an independent oracle instead of a tautology.
_CONTENT_RECT_JS = """
    const video = document.querySelector('.annotation-player-container video');
    const v = video.getBoundingClientRect();
    let frame = {x: v.x, y: v.y, width: v.width, height: v.height};
    if (video.videoWidth > 0 && video.videoHeight > 0) {
        const scale = Math.min(v.width / video.videoWidth, v.height / video.videoHeight);
        const width = video.videoWidth * scale;
        const height = video.videoHeight * scale;
        frame = {
            x: v.x + (v.width - width) / 2,
            y: v.y + (v.height - height) / 2,
            width: width,
            height: height,
        };
    }
"""


def _video_content_rect(page):
    return page.evaluate("() => {" + _CONTENT_RECT_JS + "return frame;}")


def _blur_position_relative_to_video(page):
    # Express the blur box's on-screen geometry as a fraction of the video's *picture*, so it
    # can be compared across resizes regardless of how large the video is currently rendered.
    return page.evaluate(
        """() => {
            """
        + _CONTENT_RECT_JS
        + """
            const b = document.querySelector('#blur-overlay-1').getBoundingClientRect();
            return {
                x: (b.left - frame.x) / frame.width,
                y: (b.top - frame.y) / frame.height,
                width: b.width / frame.width,
                height: b.height / frame.height,
            };
        }"""
    )


def _assert_relative_positions_match(before, after):
    assert before["x"] == pytest.approx(after["x"], abs=0.02)
    assert before["y"] == pytest.approx(after["y"], abs=0.02)
    assert before["width"] == pytest.approx(after["width"], abs=0.02)
    assert before["height"] == pytest.approx(after["height"], abs=0.02)


def _assert_annotation_box_covers_the_picture(page):
    # The load-bearing invariant of the whole feature: the annotation overlay is exactly the
    # visible picture, so a stored percentage means "percent of what the viewer can see".
    rects = page.evaluate(
        """() => {
            """
        + _CONTENT_RECT_JS
        + """
            const box = document.querySelector('.annotation-box').getBoundingClientRect();
            return {frame: frame, box: {x: box.x, y: box.y, width: box.width, height: box.height}};
        }"""
    )
    frame, box = rects["frame"], rects["box"]
    assert box["x"] == pytest.approx(frame["x"], abs=1)
    assert box["y"] == pytest.approx(frame["y"], abs=1)
    assert box["width"] == pytest.approx(frame["width"], abs=1)
    assert box["height"] == pytest.approx(frame["height"], abs=1)


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
    # roughly 2.67:1) is the mirror image of the other fullscreen test: height,
    # not width, is the constraining dimension, so the picture must pillarbox
    # with bars down the left and right rather than stretching edge to edge.
    _open_player(page, live_server, viewport={"width": 1600, "height": 600})
    _inject_blur_annotation(page)

    before = _blur_position_relative_to_video(page)

    page.locator(".fullscreen-btn").click()
    page.wait_for_function("() => !!document.fullscreenElement", timeout=2000)
    page.wait_for_timeout(300)

    frame = _video_content_rect(page)
    viewport = page.viewport_size

    # Height fills the screen; width is narrower than the screen (proving
    # this didn't just vacuously stretch edge-to-edge) and centered within it.
    assert frame["height"] == pytest.approx(viewport["height"], abs=2)
    assert frame["width"] < viewport["width"] - 2
    expected_x = (viewport["width"] - frame["width"]) / 2
    assert frame["x"] == pytest.approx(expected_x, abs=2)

    _assert_annotation_box_covers_the_picture(page)

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

    frame = _video_content_rect(page)
    viewport = page.viewport_size

    assert frame["width"] == pytest.approx(viewport["width"], abs=2)
    assert frame["height"] < viewport["height"] - 2
    expected_y = (viewport["height"] - frame["height"]) / 2
    assert frame["y"] == pytest.approx(expected_y, abs=2), (
        "video should be vertically centered in the letterboxed space, not "
        "pinned to the top of the screen"
    )

    _assert_annotation_box_covers_the_picture(page)

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
    # exact match (no letterboxing) while also proving the picture actually
    # scales up past its native pixel size rather than capping out at 1280px.
    #
    # Note this is asserted on the *picture*, not the video element's box: under
    # `object-fit: contain` the element box always fills its wrapper, so an
    # element-box assertion here would pass vacuously.
    _open_player(page, live_server, viewport={"width": 2000, "height": 1125})
    _inject_blur_annotation(page)

    before = _blur_position_relative_to_video(page)

    page.locator(".fullscreen-btn").click()
    page.wait_for_function("() => !!document.fullscreenElement", timeout=2000)
    page.wait_for_timeout(300)

    frame = _video_content_rect(page)
    viewport = page.viewport_size

    assert frame["width"] == pytest.approx(viewport["width"], abs=2)
    assert frame["height"] == pytest.approx(viewport["height"], abs=2)
    assert frame["x"] == pytest.approx(0, abs=2)
    assert frame["y"] == pytest.approx(0, abs=2)
    assert frame["width"] > 1280, (
        "picture did not scale past the source's native width, so this would "
        "pass vacuously on a viewport smaller than the source"
    )

    # With no bars to leave room for, the overlay should be flush to the video element. Asserted
    # against the measured boxes rather than the inline `inset` string, because the overlay is
    # positioned by explicit left/top/width/height now -- the video element is not always the same
    # rectangle as its wrapper, so a symmetric inset could not express where the picture is.
    offsets = page.evaluate(
        """() => {
            const video = document.querySelector('.annotation-player-container video')
                .getBoundingClientRect();
            const box = document.querySelector('.annotation-box').getBoundingClientRect();
            return {left: box.x - video.x, top: box.y - video.y,
                    right: (video.x + video.width) - (box.x + box.width),
                    bottom: (video.y + video.height) - (box.y + box.height)};
        }"""
    )
    for side, value in offsets.items():
        assert value == pytest.approx(0, abs=1), (
            f"{side} pad of {value} with no bars to leave"
        )

    after = _blur_position_relative_to_video(page)
    _assert_relative_positions_match(before, after)


@pytest.mark.parametrize(
    "viewport",
    [
        {"width": 1600, "height": 600},
        {"width": 300, "height": 1600},
        {"width": 900, "height": 900},
    ],
    ids=["ultrawide", "portrait", "square"],
)
def test_the_picture_is_never_distorted(page, live_server, seeded_demo_data, viewport):
    """The guarantee the whole sizing approach exists to provide.

    The video is `object-fit: contain`, so the browser - not our layout code - owns the
    aspect ratio, and the overlay is sized to the resulting picture. Together those mean a
    sizing mistake can only ever misplace the overlay slightly; it can never stretch the
    picture. This asserts both halves at aspect ratios far from the source's own 16:9.
    """
    _open_player(page, live_server, viewport=viewport)
    _inject_blur_annotation(page)

    measured = page.evaluate(
        """() => {
            const video = document.querySelector('.annotation-player-container video');
            const box = document.querySelector('.annotation-box').getBoundingClientRect();
            return {
                objectFit: getComputedStyle(video).objectFit,
                intrinsicRatio: video.videoWidth / video.videoHeight,
                boxRatio: box.width / box.height,
            };
        }"""
    )

    assert measured["objectFit"] == "contain", (
        "the picture's aspect ratio must be the browser's responsibility, not ours"
    )
    # And the overlay must track that undistorted picture, not the element box.
    assert measured["boxRatio"] == pytest.approx(measured["intrinsicRatio"], rel=0.01)


def test_blur_radius_scales_with_the_rendered_frame(
    page, live_server, seeded_demo_data
):
    # A fixed pixel radius obscures proportionally less of the picture the larger the video is
    # rendered, so blur strength is derived from frame height instead.
    _open_player(page, live_server, viewport={"width": 1280, "height": 720})
    _inject_blur_annotation(page)

    read_radius = """() => {
        const value = getComputedStyle(document.querySelector('#blur-overlay-1')).backdropFilter;
        const match = value.match(/blur\\(([\\d.]+)px\\)/);
        return match ? parseFloat(match[1]) : null;
    }"""

    large = page.evaluate(read_radius)
    assert large, f"expected a px blur radius, got {large}"

    page.set_viewport_size({"width": 500, "height": 380})
    page.wait_for_timeout(200)
    small = page.evaluate(read_radius)

    assert small < large, (
        f"blur radius did not shrink with the frame ({small} vs {large})"
    )


def test_the_blur_glides_between_positions(page, live_server, seeded_demo_data):
    """A blur with several positions interpolates between them rather than snapping.

    This is a safety property, not a nicety: a box that snaps to the last position passed lags
    behind whatever it is covering, briefly exposing it. Interpolating is what makes a handful
    of positions sufficient to keep a moving subject hidden.
    """
    _open_player(page, live_server, viewport={"width": 1280, "height": 720})

    # Two positions four seconds apart, moving right and growing. Every field differs so a
    # field mix-up cannot cancel out.
    page.evaluate(
        """() => {
            window.videoPlayer.loadData({
                annotations: [{
                    id: 1,
                    type: 'blur',
                    start: 0,
                    end: 999999,
                    positions: [
                        {id: 1, time: 0, x: 10, y: 20, width: 20, height: 10},
                        {id: 2, time: 4, x: 50, y: 40, width: 30, height: 20},
                    ],
                }],
            });
        }"""
    )
    page.wait_for_selector("#blur-overlay-1", state="attached")

    def seek_and_measure(time):
        page.evaluate(f"() => window.videoPlayer.setCurrentTime({time})")
        page.wait_for_timeout(200)
        return _blur_position_relative_to_video(page)

    # Exactly halfway: every field should be halfway too. Snapping would report the t=0 values
    # (0.10 / 0.20 / 0.20 / 0.10) instead.
    midpoint = seek_and_measure(2.0)
    assert midpoint["x"] == pytest.approx(0.30, abs=0.01)
    assert midpoint["y"] == pytest.approx(0.30, abs=0.01)
    assert midpoint["width"] == pytest.approx(0.25, abs=0.01)
    assert midpoint["height"] == pytest.approx(0.15, abs=0.01)

    # A quarter of the way, to prove it is genuinely proportional and not just a 50% special case.
    quarter = seek_and_measure(1.0)
    assert quarter["x"] == pytest.approx(0.20, abs=0.01)

    # Past the last position the geometry holds rather than extrapolating off-frame.
    beyond = seek_and_measure(6.0)
    assert beyond["x"] == pytest.approx(0.50, abs=0.01)
    assert beyond["width"] == pytest.approx(0.30, abs=0.01)


def test_blur_stays_aligned_with_the_subtitle_sidebar_open(
    page, live_server, seeded_demo_data
):
    # Opening the sidebar shrinks the video via a margin on .video-wrapper, animated over
    # 0.3s. The overlay has to follow it, which is why the wrapper uses `align-self: stretch`
    # (stretch fills the container minus margins) rather than an explicit width.
    _open_player(page, live_server, viewport={"width": 1280, "height": 720})
    _inject_blur_annotation(page)

    before = _blur_position_relative_to_video(page)
    width_before = _video_content_rect(page)["width"]

    page.locator(".subtitle-sidebar-btn").click()
    page.wait_for_timeout(600)  # outlast the 0.3s margin transition

    width_after = _video_content_rect(page)["width"]
    assert width_after < width_before, (
        "the sidebar did not actually shrink the video, so this would pass vacuously"
    )

    _assert_annotation_box_covers_the_picture(page)
    _assert_relative_positions_match(before, _blur_position_relative_to_video(page))
