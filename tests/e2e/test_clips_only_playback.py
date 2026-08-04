import pytest

from core.models import Content

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.django_db(transaction=True),
]


def _enable_clips_only_on_birds_overview():
    # "Birds Overview" (seeded by seed_demo_data) has exactly one clip,
    # 2.5s-18.0s, on a ~20s video - giving a "before the clip" and "after the
    # clip" dead zone to redirect out of.
    content = Content.objects.get(title="Birds Overview")
    content.clips_only = True
    content.save()
    return content


def _enable_clips_only_on_content_with_no_clips():
    # "Birds Draft Discussion" shares the real birds.mp4 file but has no
    # annotation_set at all, so it has zero clips.
    content = Content.objects.get(title="Birds Draft Discussion")
    content.clips_only = True
    content.save()
    return content


def _wait_for_video_ready(page):
    page.wait_for_function(
        """() => {
            const video = document.querySelector('.annotation-player-container video');
            return video && !isNaN(video.duration) && video.duration > 0;
        }""",
        timeout=5000,
    )


def test_clips_only_playback_pauses_at_the_end_of_a_clip_without_flashing_a_violation_warning(
    page, live_server, seeded_demo_data
):
    # Distinguishes ordinary playback reaching the natural end of a clip from
    # a deliberate out-of-clip seek (see the scrubber-click test below): the
    # restriction warning should only flash when a student actually tries to
    # go somewhere disallowed, not every time a clip's playback simply ends.
    content = _enable_clips_only_on_birds_overview()

    page.goto(f"{live_server.url}/login/dev/quick/")
    page.goto(f"{live_server.url}/player/{content.pk}/")
    _wait_for_video_ready(page)

    result = page.evaluate(
        """async () => {
            const video = document.querySelector('.annotation-player-container video');
            video.muted = true;
            // Start just inside the clip, close to its 18.0s end, so ordinary
            // playback runs off the end of the clip within this test's window.
            video.currentTime = 17.7;
            await video.play().catch(() => {});
            await new Promise((resolve) => setTimeout(resolve, 800));
            const clipsBtn = document.querySelector('.clips-btn');
            const markers = Array.from(document.querySelectorAll('.clip-on-scrubber'));
            return {
                paused: video.paused,
                time: video.currentTime,
                anyFlashing: (!!clipsBtn && clipsBtn.classList.contains('clip-restriction-flash'))
                    || markers.some((el) => el.classList.contains('clip-restriction-flash')),
            };
        }"""
    )

    assert result["paused"], (
        "playback should pause once it runs off the end of the clip"
    )
    assert result["time"] == pytest.approx(18.0, abs=0.5), (
        "playback that reaches the end of the only clip should stay there, "
        "not jump anywhere else"
    )
    assert not result["anyFlashing"], (
        "reaching a clip's natural end during ordinary playback is not a restriction "
        "violation and should not flash the warning"
    )


def test_clips_only_scrubber_click_outside_clip_snaps_into_it_and_flashes_highlights(
    page, live_server, seeded_demo_data
):
    content = _enable_clips_only_on_birds_overview()

    page.goto(f"{live_server.url}/login/dev/quick/")
    page.goto(f"{live_server.url}/player/{content.pk}/")
    _wait_for_video_ready(page)

    scrubber = page.locator(".annotation-player-container .scrubber")
    box = scrubber.bounding_box()
    # Click near the very start of the scrubber - the clip begins at 2.5s
    # into a ~20s video, so this lands well before the clip's start. Uses the
    # locator's own click (which scrolls the element into view first) rather
    # than raw page.mouse.click at absolute page coordinates - the scrubber
    # sits below the fold on the default viewport size, so a raw click there
    # silently misses it.
    scrubber.click(position={"x": 2, "y": box["height"] / 2})

    result = page.evaluate(
        """() => {
            const clipsBtn = document.querySelector('.clips-btn');
            const markers = Array.from(document.querySelectorAll('.clip-on-scrubber'));
            return {
                time: document.querySelector('.annotation-player-container video').currentTime,
                clipsBtnFlashing: !!clipsBtn && clipsBtn.classList.contains('clip-restriction-flash'),
                markerCount: markers.length,
                allMarkersFlashing: markers.length > 0
                    && markers.every((el) => el.classList.contains('clip-restriction-flash')),
            };
        }"""
    )

    assert result["time"] == pytest.approx(2.5, abs=0.5), (
        "clicking the scrubber outside the clip should snap into it"
    )
    assert result["clipsBtnFlashing"], (
        "the clips button should flash yellow when a seek is redirected outside every clip"
    )
    assert result["markerCount"] > 0, (
        "every clip should be rendered as a scrubber marker"
    )
    assert result["allMarkersFlashing"], (
        "every clip-on-scrubber marker should flash, not just the active one"
    )


def test_clips_only_does_not_restrict_playback_in_the_editor(
    page, live_server, seeded_demo_data
):
    # clips_only is a student-facing playback constraint; the editor must
    # always be able to play/scrub the full video regardless of it.
    content = _enable_clips_only_on_birds_overview()

    page.goto(f"{live_server.url}/login/dev/quick/")
    page.goto(f"{live_server.url}/video-editor/{content.pk}/")
    _wait_for_video_ready(page)

    result = page.evaluate(
        """async () => {
            const video = document.querySelector('.annotation-player-container video');
            video.muted = true;
            // This is well outside the one defined clip (2.5s-18.0s) - in
            // clips_only student playback this would immediately redirect
            // back into the clip and pause.
            video.currentTime = 19.0;
            await video.play().catch(() => {});
            await new Promise((resolve) => setTimeout(resolve, 500));
            const advanced = video.currentTime > 19.0;
            video.pause();
            return { advanced, time: video.currentTime };
        }"""
    )

    assert result["advanced"], (
        "the editor should not be redirected/paused by clips_only playback restrictions"
    )


def test_clips_only_with_no_clips_defined_blocks_playback_with_a_message(
    page, live_server, seeded_demo_data
):
    content = _enable_clips_only_on_content_with_no_clips()

    page.goto(f"{live_server.url}/login/dev/quick/")
    page.goto(f"{live_server.url}/player/{content.pk}/")
    _wait_for_video_ready(page)

    result = page.evaluate(
        """async () => {
            const video = document.querySelector('.annotation-player-container video');
            video.muted = true;
            // The player's clipsOnly enforcement reacts to the "playing" event
            // and immediately calls pause() once it sees there are no clips to
            // play, which aborts this play() request in Chromium - expected
            // here, so it's swallowed rather than left to fail the test.
            await video.play().catch(() => {});
            await new Promise((resolve) => setTimeout(resolve, 500));
            const messageBox = document.querySelector('.annotation-box');
            return {
                paused: video.paused,
                time: video.currentTime,
                message: messageBox ? messageBox.innerText : null,
            };
        }"""
    )

    assert result["paused"], "clips_only with no clips defined must block playback"
    assert result["time"] == pytest.approx(0, abs=0.5), (
        "playback should not advance at all when there are no clips to restrict to"
    )
    assert "no clips have been defined" in result["message"]
