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


def test_clips_only_playback_redirects_into_the_clip_when_started_outside_it(
    page, live_server, seeded_demo_data
):
    content = _enable_clips_only_on_birds_overview()

    page.goto(f"{live_server.url}/login/dev/quick/")
    page.goto(f"{live_server.url}/player/{content.pk}/")
    _wait_for_video_ready(page)

    result = page.evaluate(
        """async () => {
            const video = document.querySelector('.annotation-player-container video');
            video.muted = true;
            // Seek past the clip's end (18.0s), into the dead zone after it.
            video.currentTime = 19.0;
            // The player's clipsOnly enforcement reacts to the "playing" event
            // and immediately calls pause() once it sees we're outside every
            // clip, which aborts this play() request in Chromium - expected
            // here, so it's swallowed rather than left to fail the test.
            await video.play().catch(() => {});
            await new Promise((resolve) => setTimeout(resolve, 500));
            return { paused: video.paused, time: video.currentTime };
        }"""
    )

    assert result["paused"], "clips_only playback should pause once redirected"
    assert result["time"] == pytest.approx(2.5, abs=0.5), (
        "playback starting outside the clip should snap to the clip's start"
    )


def test_clips_only_scrubber_click_outside_clip_snaps_into_it(
    page, live_server, seeded_demo_data
):
    content = _enable_clips_only_on_birds_overview()

    page.goto(f"{live_server.url}/login/dev/quick/")
    page.goto(f"{live_server.url}/player/{content.pk}/")
    _wait_for_video_ready(page)

    scrubber = page.locator(".annotation-player-container .scrubber")
    box = scrubber.bounding_box()
    # Click near the very start of the scrubber - the clip begins at 2.5s
    # into a ~20s video, so this lands well before the clip's start.
    page.mouse.click(box["x"] + 2, box["y"] + box["height"] / 2)

    current_time = page.evaluate(
        "() => document.querySelector('.annotation-player-container video').currentTime"
    )
    assert current_time == pytest.approx(2.5, abs=0.5), (
        "clicking the scrubber outside the clip should snap into it"
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
            // See test_clips_only_playback_redirects_into_the_clip_when_started_outside_it
            // for why the AbortError from this play() request is expected and swallowed.
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
