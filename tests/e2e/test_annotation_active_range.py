"""When an annotation counts as active, and how often the player repaints.

`applyAnnotations` shares one active-range predicate across every annotation type and reschedules
itself while playing. Both are easy to break in ways a geometry test would not notice: a range that
includes the end time makes a skip seek to a moment where it is still active, and any extra
unguarded entry point starts a second animation loop that nothing holds a handle to. These tests
pin the observable consequences rather than the predicate itself.

The boundary cases here seek first and then build the annotation around the playhead's *observed*
time, because `applyAnnotations` reads `video.currentTime` and a browser is free to land a seek on
a frame boundary rather than the requested value. Asserting "exactly at the end time" against a
requested time would be testing the seek, and intermittently.
"""

import pytest

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.django_db(transaction=True),
]

VIDEO = ".annotation-player-container video"


def _open_paused_player(page, open_player):
    """Paused for the whole of every test here: while playing, applyAnnotations reschedules itself
    every frame by design, which would drown out the repaint counting below.
    """
    content = open_player()
    page.evaluate("(video) => document.querySelector(video).pause()", VIDEO)
    return content


def _load_annotations(page, annotations):
    """Load synthetic annotations through the player's public API.

    The behaviour under test is entirely inside applyAnnotations, so authoring these through the
    editor would only add ways for the test to fail for reasons that are not the subject.
    """
    page.evaluate(
        "(annotations) => window.videoPlayer.loadData({annotations})", annotations
    )


def _seek(page, seconds):
    """Seek, then report where the playhead actually landed."""
    page.evaluate("(t) => window.videoPlayer.setCurrentTime(t)", seconds)
    page.wait_for_function(
        "([video, t]) => Math.abs(document.querySelector(video).currentTime - t) < 0.2",
        arg=[VIDEO, seconds],
        timeout=5000,
    )
    return page.evaluate("(video) => document.querySelector(video).currentTime", VIDEO)


def _count_repaints(page):
    """Wrap applyAnnotations so a test can see how many times it runs.

    Counting calls, rather than watching currentTime, is what makes the runaway case visible: a
    skip that reseeks to the time the playhead is already at changes nothing observable except
    that it keeps on doing it.
    """
    page.evaluate(
        """() => {
            const player = window.videoPlayer;
            const original = player.applyAnnotations.bind(player);
            window.__repaints = 0;
            // Assigned on the instance, so the recursive `this.applyAnnotations()` calls made by
            // requestAnimationFrame and by skipTo are counted too.
            player.applyAnnotations = function () {
                window.__repaints += 1;
                return original();
            };
        }"""
    )


def _repaints(page):
    return page.evaluate("() => window.__repaints")


def _overlay_percentages(page):
    return page.evaluate(
        """() => {
            const overlay = document.querySelector('#blur-overlay-1');
            if (!overlay) return null;
            const style = overlay.style;
            return {
                x: parseFloat(style.left),
                y: parseFloat(style.top),
                width: parseFloat(style.width),
                height: parseFloat(style.height),
            };
        }"""
    )


def test_a_skip_at_its_own_end_time_stops_repainting(page, open_player):
    """A skip sends the playhead to its end time, so counting that moment as active is fatal.

    The skip pauses, seeks to where the playhead already is, and queues another repaint - which
    finds the skip active and does it again. Being paused does not stop it: the frame skipTo
    queues is not the one the paused check at the end of applyAnnotations declines to queue.
    """
    _open_paused_player(page, open_player)
    landed = _seek(page, 8.0)
    _load_annotations(page, [{"id": 1, "type": "skip", "start": 4.0, "end": landed}])
    _count_repaints(page)

    # Nothing is touching the player now, so whatever repaints happen are self-inflicted.
    page.wait_for_timeout(300)
    settled = _repaints(page)
    page.wait_for_timeout(500)
    later = _repaints(page)

    assert later == settled, (
        f"the skip is still rescheduling itself at its end time: {later - settled} repaints in "
        f"half a second with nothing else happening"
    )
    assert page.evaluate(
        "(video) => document.querySelector(video).currentTime", VIDEO
    ) == pytest.approx(landed, abs=0.01), (
        "the skip seeked away from a time it had finished at"
    )


def test_a_blur_still_covers_its_subject_at_its_own_end_time(page, open_player):
    """The one type that does need its end time counted as active.

    A blur exists to conceal something, so the last frame it claims has to stay covered. Every
    other type turns off at its end time; this one is deliberately the exception, which is why
    the range cannot simply be shared.
    """
    _open_paused_player(page, open_player)
    landed = _seek(page, 6.0)
    _load_annotations(
        page,
        [
            {
                "id": 1,
                "type": "blur",
                "start": 2.0,
                "end": landed,
                "positions": [
                    {
                        "id": 1,
                        "time": 2.0,
                        "x": 10.0,
                        "y": 20.0,
                        "width": 20.0,
                        "height": 10.0,
                    },
                    {
                        "id": 2,
                        "time": landed,
                        "x": 50.0,
                        "y": 40.0,
                        "width": 30.0,
                        "height": 20.0,
                    },
                ],
            }
        ],
    )

    assert _overlay_percentages(page) == {
        "x": 50.0,
        "y": 40.0,
        "width": 30.0,
        "height": 20.0,
    }, "the blur stopped covering its subject on the last frame it claims"


def test_a_blur_stops_covering_after_its_end_time(page, open_player):
    """The other half of the case above: including the end time is one frame's grace, not an
    open-ended window."""
    _open_paused_player(page, open_player)
    landed = _seek(page, 6.0)
    _load_annotations(
        page,
        [
            {
                "id": 1,
                "type": "blur",
                "start": 2.0,
                "end": landed - 0.5,
                "positions": [
                    {
                        "id": 1,
                        "time": 2.0,
                        "x": 10.0,
                        "y": 20.0,
                        "width": 20.0,
                        "height": 10.0,
                    }
                ],
            }
        ],
    )

    assert _overlay_percentages(page) is None, "the blur outlived its own time range"
