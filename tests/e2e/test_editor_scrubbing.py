from playwright.sync_api import expect
import pytest

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.django_db(transaction=True),
]


def _open_editor(page, live_server):
    from core.models import Content

    content = Content.objects.get(title="Birds Overview")

    page.goto(f"{live_server.url}/login/dev/quick/")
    page.goto(f"{live_server.url}/video-editor/{content.pk}/")

    # Wait until the video metadata has loaded and the Editor has rendered ticks
    # (renderTickMarksAndLabels + attachTimelineListeners run together in init).
    page.wait_for_function(
        """() => {
            const video = document.querySelector('.annotation-player-container video');
            const ticks = document.getElementById('tick-marks-container');
            return video && !isNaN(video.duration) && video.duration > 0
                && ticks && ticks.children.length > 0;
        }""",
        timeout=30000,
    )
    return content


def _current_time(page):
    return page.evaluate(
        "() => document.querySelector('.annotation-player-container video').currentTime"
    )


def _duration(page):
    return page.evaluate(
        "() => document.querySelector('.annotation-player-container video').duration"
    )


def test_clicking_ticks_row_seeks_to_that_time(page, live_server, seeded_demo_data):
    _open_editor(page, live_server)

    assert _current_time(page) == pytest.approx(0, abs=0.5)

    ticks = page.locator("#timeline-ticks-content")
    box = ticks.bounding_box()
    # Click three-quarters of the way across the timeline.
    page.mouse.click(box["x"] + box["width"] * 0.75, box["y"] + box["height"] / 2)

    duration = _duration(page)
    expected = duration * 0.75
    # Generous tolerance: click precision + video keyframe snapping.
    assert _current_time(page) == pytest.approx(expected, abs=duration * 0.1)


def test_clicking_empty_track_row_seeks(page, live_server, seeded_demo_data):
    _open_editor(page, live_server)

    row = page.locator(".timeline-track-row-right").first
    box = row.bounding_box()
    # Click near the far left of the track row, away from any annotation item,
    # then near the far right, and confirm the time tracks the click position.
    page.mouse.click(box["x"] + box["width"] * 0.1, box["y"] + 2)
    left_time = _current_time(page)

    page.mouse.click(box["x"] + box["width"] * 0.9, box["y"] + 2)
    right_time = _current_time(page)

    duration = _duration(page)
    assert left_time == pytest.approx(duration * 0.1, abs=duration * 0.1)
    assert right_time > left_time


def test_clicking_annotation_content_seeks_to_its_start_not_click_position(
    page, live_server, seeded_demo_data
):
    _open_editor(page, live_server)

    item = page.locator(".track-item").first
    content = item.locator(".track-item-content")
    expect(content).to_be_visible()

    start_time = float(item.get_attribute("data-start"))

    # Click near the right edge of the annotation's content. If the timeline
    # scrubber wrongly handled this, the playhead would land at the click
    # position; instead it must land on the annotation's start (selection).
    box = content.bounding_box()
    click_x = box["x"] + box["width"] * 0.85
    content.click(position={"x": box["width"] * 0.85, "y": box["height"] / 2})

    ticks = page.locator("#timeline-ticks-content")
    tbox = ticks.bounding_box()
    duration = _duration(page)
    click_position_time = ((click_x - tbox["x"]) / tbox["width"]) * duration

    # The click position maps to a clearly different time than the start, so
    # this distinguishes "seek to annotation start" from "scrub to click".
    assert abs(click_position_time - start_time) > 1.0
    assert _current_time(page) == pytest.approx(start_time, abs=0.2)


def test_scrubber_moves_smoothly_during_playback(page, live_server, seeded_demo_data):
    _open_editor(page, live_server)

    # Play for a short window and record the scrubber's position on every
    # animation frame. A timeupdate-only scrubber lurches (only ~4 distinct
    # positions/sec); a frame-driven one produces many distinct positions.
    result = page.evaluate(
        """async () => {
            const video = document.querySelector('.annotation-player-container video');
            const scrubber = document.getElementById('editor-scrubber');
            video.muted = true;
            video.currentTime = 0;
            await video.play();
            const startTime = video.currentTime;
            const positions = new Set();
            await new Promise((resolve) => {
                const startTs = performance.now();
                const tick = () => {
                    positions.add(getComputedStyle(scrubber).transform);
                    if (performance.now() - startTs > 800) resolve();
                    else requestAnimationFrame(tick);
                };
                requestAnimationFrame(tick);
            });
            const advanced = video.currentTime - startTime;
            video.pause();
            return { distinct: positions.size, advanced, usesTransform: getComputedStyle(scrubber).transform !== 'none' };
        }"""
    )

    assert result["advanced"] > 0.2, "video did not actually play"
    assert result["usesTransform"], "scrubber is not positioned via transform"
    # Far more distinct positions than the handful timeupdate alone would give.
    assert result["distinct"] > 10


def test_player_progress_scrubber_moves_smoothly_during_playback(
    page, live_server, seeded_demo_data
):
    # The AnnotationPlayer's control bar (with .scrubber-progress) is embedded in
    # the editor page, so we can exercise the shared per-frame animation here.
    _open_editor(page, live_server)

    result = page.evaluate(
        """async () => {
            const video = document.querySelector('.annotation-player-container video');
            const progress = document.querySelector('.scrubber-progress');
            video.muted = true;
            video.currentTime = 0;
            await video.play();
            const startTime = video.currentTime;
            const positions = new Set();
            await new Promise((resolve) => {
                const startTs = performance.now();
                const tick = () => {
                    positions.add(getComputedStyle(progress).transform);
                    if (performance.now() - startTs > 800) resolve();
                    else requestAnimationFrame(tick);
                };
                requestAnimationFrame(tick);
            });
            const advanced = video.currentTime - startTime;
            video.pause();
            return { distinct: positions.size, advanced };
        }"""
    )

    assert result["advanced"] > 0.2, "video did not actually play"
    # Frame-driven scaleX fill produces many distinct positions, not a lurch.
    assert result["distinct"] > 10
