"""Skips in the editor take effect during playback but not while paused, so editors can inspect
the footage a skip hides. The student player honors skips either way.
"""

import pytest

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.django_db(transaction=True),
]

VIDEO = ".annotation-player-container video"
# Long enough that plain playback from inside it cannot reach its end within PLAYBACK_TIMEOUT_MS.
SKIP = {"id": 1, "type": "skip", "start": 4.0, "end": 15.0}
PLAYBACK_TIMEOUT_MS = 2000


def _load_skip(page):
    page.evaluate("(skip) => window.videoPlayer.loadData({annotations: [skip]})", SKIP)


def _pause(page):
    page.evaluate("(video) => document.querySelector(video).pause()", VIDEO)


def _settled_time(page):
    page.wait_for_function(
        "(video) => !document.querySelector(video).seeking", arg=VIDEO, timeout=5000
    )
    # Lets the frame skipTo queues run applyAnnotations.
    page.wait_for_timeout(200)
    return page.evaluate("(video) => document.querySelector(video).currentTime", VIDEO)


def test_paused_editor_can_seek_inside_a_skip(page, open_editor):
    open_editor()
    _pause(page)
    _load_skip(page)

    page.evaluate("() => window.videoPlayer.setCurrentTime(6.0)")

    assert _settled_time(page) == pytest.approx(6.0, abs=0.2)


def test_paused_editor_can_drag_scrubber_inside_a_skip(page, open_editor):
    open_editor()
    _pause(page)
    _load_skip(page)

    duration = page.evaluate("(video) => document.querySelector(video).duration", VIDEO)
    box = page.locator(".annotation-player-container .scrubber").bounding_box()
    y = box["y"] + box["height"] / 2
    target = 5.0
    page.mouse.move(box["x"] + 1, y)
    page.mouse.down()
    page.mouse.move(box["x"] + box["width"] * target / duration, y, steps=5)
    # Read before release: the click that follows mouseup seeks on its own.
    during_drag = page.evaluate(
        "(video) => document.querySelector(video).currentTime", VIDEO
    )
    page.mouse.up()

    assert during_drag == pytest.approx(target, abs=duration * 0.02)
    assert _settled_time(page) == pytest.approx(target, abs=duration * 0.02)


def test_playing_editor_still_honors_skips(page, open_editor):
    open_editor()
    _pause(page)
    _load_skip(page)
    page.evaluate("() => window.videoPlayer.setCurrentTime(6.0)")
    _settled_time(page)

    page.evaluate("() => window.videoPlayer.play()")

    page.wait_for_function(
        "([video, end]) => document.querySelector(video).currentTime >= end",
        arg=[VIDEO, SKIP["end"]],
        timeout=PLAYBACK_TIMEOUT_MS,
    )


def test_paused_player_still_honors_skips(page, open_player):
    open_player()
    _pause(page)
    _load_skip(page)

    page.evaluate("() => window.videoPlayer.setCurrentTime(6.0)")

    assert _settled_time(page) == pytest.approx(SKIP["end"], abs=0.2)
