"""Annotations follow a seek on YouTube-backed content, including while paused.

The IFrame Player API emits nothing for a seek, and nothing at all while paused - so
YouTubeVideoElement synthesizes the seeking/timeupdate/seeked sequence a <video> would emit
(`_beginSeek`). Everything downstream is built on that cadence: AnnotationPlayer re-applies
annotations on `seeked`, BlurEditor's rig and active-point highlight track `timeupdate`/`seeked`
and flush a pending nudge on `seeking`, and the editor's comment rig and scrubber use the same
pair.

Without it, scrubbing a paused YouTube video left every overlay painted for the time the playhead
used to be at. That is not a cosmetic bug: the blur box an instructor then drags is drawn for the
wrong frame, so the position that gets stored describes a moment nobody was looking at.

Run against the fake IFrame API, because the behaviour under test is ours and needs to be
deterministic - a real seek settles whenever YouTube's network and keyframes allow.
"""

import pytest

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.django_db(transaction=True),
]

# Two points far apart, so the interpolated box at a seeked-to time is unmistakably different from
# the box at t=0 rather than a rounding away from it.
BLUR = {
    "id": 1,
    "type": "blur",
    "start": 0,
    "end": 999999,
    "positions": [
        {"id": 1, "time": 0, "x": 5, "y": 5, "width": 20, "height": 20},
        {"id": 2, "time": 30, "x": 60, "y": 60, "width": 20, "height": 20},
    ],
}
SEEK_TO_SECONDS = 20
# Linear interpolation between the two points above: 5 + (20/30) * (60 - 5).
EXPECTED_LEFT_PERCENT = 5 + (SEEK_TO_SECONDS / 30) * 55


def _open_player(page, live_server, content):
    page.goto(f"{live_server.url}/login/dev/quick/")
    page.goto(f"{live_server.url}/player/{content.pk}/")
    page.wait_for_function(
        """() => {
            const yt = document.querySelector('youtube-video');
            return Boolean(window.videoPlayer && yt && yt.querySelector('iframe')
                && !isNaN(yt.duration) && yt.duration > 0);
        }""",
        timeout=15000,
    )


def _load_blur(page):
    page.evaluate(
        """(blur) => {
            window.videoPlayer.loadData({annotations: [blur]});
            window.videoPlayer.applyAnnotations();
        }""",
        BLUR,
    )
    page.wait_for_selector("#blur-overlay-1", state="attached")


def _overlay_left(page):
    return page.eval_on_selector("#blur-overlay-1", "el => parseFloat(el.style.left)")


def _pause_after_playing(page):
    # Paused *after* real playback, not merely never started: an unstarted player behaves
    # differently, and the reported bug was about scrubbing a video someone had been watching.
    page.evaluate("() => window.videoPlayer.play()")
    page.wait_for_function("() => !document.querySelector('#video-player').paused")
    page.wait_for_timeout(400)
    page.evaluate("() => window.videoPlayer.pause()")
    page.wait_for_function("() => document.querySelector('#video-player').paused")


def test_blur_overlay_follows_a_paused_seek(
    fake_youtube, live_server, youtube_content, page
):
    _open_player(page, live_server, youtube_content("Paused Seek - Overlay"))
    _load_blur(page)
    _pause_after_playing(page)

    before = _overlay_left(page)
    page.evaluate("(t) => window.videoPlayer.setCurrentTime(t)", SEEK_TO_SECONDS)
    page.wait_for_function(
        "(expected) => Math.abs(parseFloat("
        "document.querySelector('#blur-overlay-1').style.left) - expected) < 2",
        arg=EXPECTED_LEFT_PERCENT,
        timeout=5000,
    )

    after = _overlay_left(page)
    assert after != before
    assert after == pytest.approx(EXPECTED_LEFT_PERCENT, abs=2)
    assert page.evaluate("() => document.querySelector('#video-player').paused"), (
        "the seek should not have resumed playback - if it did, this test would pass on the "
        "playing-video rAF loop instead of the seeked event it is meant to cover"
    )


def test_a_paused_seek_emits_the_same_events_a_video_would(
    fake_youtube, live_server, youtube_content, page
):
    _open_player(page, live_server, youtube_content("Paused Seek - Events"))
    _pause_after_playing(page)

    page.evaluate(
        """() => {
            const el = document.querySelector('#video-player');
            window.__seen = {seeking: 0, seeked: 0, timeupdate: 0};
            for (const name of Object.keys(window.__seen)) {
                el.addEventListener(name, () => { window.__seen[name] += 1; });
            }
        }"""
    )
    page.evaluate("(t) => window.videoPlayer.setCurrentTime(t)", SEEK_TO_SECONDS)
    page.wait_for_function("() => window.__seen.seeked > 0", timeout=5000)

    seen = page.evaluate("() => window.__seen")
    assert seen["seeking"] == 1
    assert seen["seeked"] == 1
    # At least one, since the overlays repaint off timeupdate during the settle rather than only
    # once the seek completes.
    assert seen["timeupdate"] >= 1
    assert (
        page.evaluate("() => document.querySelector('#video-player').seeking") is False
    ), "seeking should be back to false once seeked has fired"


def test_ended_is_emitted_when_the_video_finishes(
    fake_youtube, live_server, youtube_content, page
):
    # `ended` distinguishes a finished video from a paused one. The IFrame API reports one state
    # for both, so the element has to emit `pause` (which stops the animation loops) *and*
    # `ended`.
    _open_player(page, live_server, youtube_content("Paused Seek - Ended"))
    page.evaluate(
        """() => {
            const el = document.querySelector('#video-player');
            window.__ended = 0;
            el.addEventListener('ended', () => { window.__ended += 1; });
        }"""
    )

    page.evaluate(
        "() => { const el = document.querySelector('#video-player');"
        " el.currentTime = el.duration - 0.2; window.videoPlayer.play(); }"
    )

    page.wait_for_function("() => window.__ended > 0", timeout=10000)
    assert page.evaluate("() => document.querySelector('#video-player').ended") is True
    assert page.evaluate("() => document.querySelector('#video-player').paused") is True
