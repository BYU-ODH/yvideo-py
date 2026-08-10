"""Resizing a timeline item by its handle, on a video long enough to expose the units.

A track item's width is a percentage of the duration, and the resize drag used to floor that width
at 1%. A percentage floor is a different amount of time for every video: a third of a second on the
30-second demo clip, and 25 seconds on a 42-minute one. So on long content - which YouTube made
routine - the first nudge of a handle snapped a 20-second annotation out to ~25 seconds, and every
attempt to drag it back short again snapped it straight back, at any zoom level. The floor is now
expressed in seconds (MIN_ITEM_SECONDS), which is the unit the user is actually resizing.

The length here matches the report: 42:17. Nothing about the bug is YouTube-specific - a
42-minute file-backed video behaves the same - but a fake YouTube player is the cheapest way to get
a video of that length into a browser, which is precisely why it went unnoticed until now.
"""

import pytest

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.django_db(transaction=True),
]

DURATION_SECONDS = 42 * 60 + 17
# 1% of the duration: the length the old floor snapped everything to.
OLD_FLOOR_SECONDS = DURATION_SECONDS / 100

# Early in the video on purpose: zooming in shows only the first minute or so of a 42-minute
# timeline, and an item outside that window cannot be dragged because it is not on screen.
ITEM_START = 20.0
ITEM_END = 40.0
# Short enough to sit under the old 1% floor, so the floor applied from the first pixel of movement.
ITEM_SECONDS = ITEM_END - ITEM_START

# Enough clicks that the item is wide on screen (1.5x per click), so the drag below is tens of
# pixels rather than the two or three that a whole-video view would make it. The bug reproduced at
# every zoom level; this only makes the assertion about *time* precise.
ZOOM_CLICKS = 8


@pytest.fixture
def long_youtube_editor(fake_youtube, live_server, youtube_content, page):
    """The editor, open on a 42-minute YouTube video with one 20-second mute annotation."""
    from core.factories import AnnotationSetFactory
    from core.factories import MuteAnnotationFactory
    from core.factories import TrackFactory

    page.add_init_script(f"window.__fakeYouTubeDurationSeconds = {DURATION_SECONDS};")
    content = youtube_content("Resize - Long Video")

    annotation_set = AnnotationSetFactory(
        name="Long Video Annotations",
        resource=content.get_resource(),
        owner=content.playlist.owner,
    )
    track = TrackFactory(annotation_set=annotation_set, name="Track 1")
    annotation = MuteAnnotationFactory(
        track=track,
        name="Mute A Long Way In",
        start_time=ITEM_START,
        end_time=ITEM_END,
    )
    content.annotation_set = annotation_set
    content.save()

    page.set_viewport_size({"width": 1400, "height": 900})
    page.goto(f"{live_server.url}/login/dev/quick/")
    page.goto(f"{live_server.url}/video-editor/{content.pk}/")
    page.wait_for_function(
        """() => {
            const yt = document.querySelector('youtube-video');
            const ticks = document.getElementById('tick-marks-container');
            return Boolean(window.videoPlayer && yt && !isNaN(yt.duration) && yt.duration > 0
                && ticks && ticks.children.length > 0);
        }""",
        timeout=15000,
    )
    for _ in range(ZOOM_CLICKS):
        page.locator("#zoom-in-button").click()
    page.wait_for_timeout(200)

    return annotation


# Not by data-annotation-id: a save versions the annotation rather than mutating the row
# (BaseAnnotation.edit), so the bar comes back carrying the *new* version's id and a selector
# written against the original stops matching the moment the drag lands. There is exactly one mute
# annotation on this timeline, so its type identifies it across versions.
ITEM_SELECTOR = '.track-item[data-annotation-type="mute"]'


def _item(page):
    return page.locator(ITEM_SELECTOR)


def _item_times(page):
    return page.evaluate(
        """(selector) => {
            const item = document.querySelector(selector);
            return {
                start: parseFloat(item.dataset.start),
                end: parseFloat(item.dataset.end),
            };
        }""",
        ITEM_SELECTOR,
    )


def _item_seconds(page):
    times = _item_times(page)
    return times["end"] - times["start"]


def _seconds_per_pixel(page):
    """How much time one pixel of the timeline is worth at the current zoom.

    The timeline is the annotations container's own box - an item's left and width are percentages
    of exactly that - so this is the only reference a drag can honestly be measured against.
    Deliberately *not* the container's scrollWidth, which an item's shrink-to-fit label overflowing
    past the end of the video inflates; a drag scaled by that lands short of the pointer by however
    much the widest label happens to overhang.

    Measured rather than assumed, so the tests below can state their expectations in seconds
    without depending on a viewport size or a zoom step.
    """
    return page.evaluate(
        """() => {
            const container = document.querySelector('.track-row-annotations-container');
            const video = document.querySelector('#video-player');
            return video.duration / container.getBoundingClientRect().width;
        }"""
    )


def _drag_handle_by(page, side, seconds):
    """Drag one resize handle by `seconds` worth of pixels, and wait for the save to land."""
    handle = _item(page).locator(f".resize-handle-{side}").bounding_box()
    dx = seconds / _seconds_per_pixel(page)
    start_x = handle["x"] + handle["width"] / 2
    start_y = handle["y"] + handle["height"] / 2
    before = page.evaluate(
        "(sel) => document.querySelector(sel).dataset.start + '/'"
        " + document.querySelector(sel).dataset.end",
        ITEM_SELECTOR,
    )

    page.mouse.move(start_x, start_y)
    page.mouse.down()
    page.mouse.move(start_x + dx, start_y, steps=8)
    page.wait_for_timeout(100)
    page.mouse.up()

    # Waiting on the times themselves rather than a fixed delay: the release saves and the
    # server's times are written back onto the bar, and a timeout here means the drag never
    # reached the handle at all - which is a different failure from resizing to the wrong length.
    page.wait_for_function(
        """([sel, before]) => {
            const item = document.querySelector(sel);
            return item && `${item.dataset.start}/${item.dataset.end}` !== before;
        }""",
        arg=[ITEM_SELECTOR, before],
        timeout=5000,
    )


def test_shrinking_a_short_item_is_not_snapped_to_one_percent_of_the_duration(
    long_youtube_editor, page
):
    assert _item_seconds(page) == pytest.approx(ITEM_SECONDS, abs=0.5)

    # Drag the right handle inward by 12 seconds: 20s -> ~8s, which is well under the 25.4s the
    # old percentage floor would have produced.
    _drag_handle_by(page, "right", -12)

    length = _item_seconds(page)
    assert length == pytest.approx(ITEM_SECONDS - 12, abs=1), (
        f"expected roughly 8s after shrinking a 20s item by 12s, got {length:.1f}s"
    )
    assert length < OLD_FLOOR_SECONDS, (
        f"the item was floored at {length:.1f}s, near 1% of the {DURATION_SECONDS}s duration "
        f"({OLD_FLOOR_SECONDS:.1f}s) - the minimum is being applied as a percentage of the "
        "video's length rather than in seconds"
    )


def test_growing_a_short_item_moves_by_the_dragged_amount(long_youtube_editor, page):
    # The other direction, which the old floor also broke - though less visibly, since it only
    # ever made items longer. Included so a fix that clamps growth instead of shrinkage cannot
    # pass.
    _drag_handle_by(page, "right", 15)

    length = _item_seconds(page)
    assert length == pytest.approx(ITEM_SECONDS + 15, abs=1), (
        f"expected roughly 35s after growing a 20s item by 15s, got {length:.1f}s"
    )


def test_the_left_handle_shortens_from_the_start(long_youtube_editor, page):
    # The left handle takes the other branch of updateResizePosition, with its own copy of the
    # floor, so it needs its own case.
    _drag_handle_by(page, "left", 12)

    start = _item_times(page)["start"]
    length = _item_seconds(page)
    assert start == pytest.approx(ITEM_START + 12, abs=1), (
        f"expected the start to move to ~{ITEM_START + 12}s, got {start:.1f}s"
    )
    assert length == pytest.approx(ITEM_SECONDS - 12, abs=1), (
        f"expected roughly 8s left after moving the start in by 12s, got {length:.1f}s"
    )
