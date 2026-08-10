"""A resize handle keeps up with the pointer that is dragging it.

Stated in pixels on purpose: no conversion to seconds anywhere below. If the pointer travels 80px
along the timeline, the edge under it should travel 80px, and that has to hold without the test
knowing anything about durations, zoom levels or percentages.

It did not. `startResize` measured the timeline as the annotations container's `scrollWidth` - the
extent of its *children* - rather than the container's own box, which is what an item's left and
width percentages are relative to. Any item whose shrink-to-fit label overhangs the end of the
video inflates that number (the seeded timeline has one: a comment near the end), so every drag was
scaled down by the overhang: on the seeded content at default zoom, roughly 12%. The drag-to-move
path had always used the container's box, so resizing and moving disagreed about what a pixel was
worth.

The error is proportional to overhang-over-container-width, so it is worst zoomed out - which is
why these run at default zoom, and why the resize tests in test_youtube_editor_item_resize.py
(zoomed far in, one short-labelled item) could not see it.
"""

import pytest

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.django_db(transaction=True),
]

# Seeded by core/dev_seed.py on the demo content's first track.
ITEM_SELECTOR = '.track-item[data-annotation-type="mute"]'
DRAG_PIXELS = 80
# A pointer-to-edge mismatch below this is sub-pixel layout and rounding through the save; the bug
# this guards against was an order of magnitude larger.
TOLERANCE_PIXELS = 6


@pytest.fixture
def editor(open_editor, page):
    page.set_viewport_size({"width": 1400, "height": 900})
    open_editor()
    page.wait_for_selector(ITEM_SELECTOR)
    # The overhanging label that inflates scrollWidth is part of the seeded data, and the whole
    # point of running here rather than on a synthetic one-item timeline. Assert it, so this stops
    # being a silent precondition if the seed changes.
    inflation = page.evaluate(
        """() => {
            const container = document.querySelector('.track-row-annotations-container');
            return container.scrollWidth - container.getBoundingClientRect().width;
        }"""
    )
    assert inflation > 20, (
        "no item's label overhangs the end of this timeline, so a scrollWidth-based drag would "
        f"be indistinguishable from a correct one here (inflation: {inflation}px)"
    )
    return page


def _item_box(page):
    return page.eval_on_selector(
        ITEM_SELECTOR, "el => el.getBoundingClientRect().toJSON()"
    )


def _drag_handle(page, side, pixels):
    handle = (
        page.locator(ITEM_SELECTOR).locator(f".resize-handle-{side}").bounding_box()
    )
    start_x = handle["x"] + handle["width"] / 2
    start_y = handle["y"] + handle["height"] / 2
    before = page.eval_on_selector(
        ITEM_SELECTOR, "el => el.dataset.start + '/' + el.dataset.end"
    )

    page.mouse.move(start_x, start_y)
    page.mouse.down()
    page.mouse.move(start_x + pixels, start_y, steps=8)
    page.wait_for_timeout(100)
    page.mouse.up()
    page.wait_for_function(
        """([sel, before]) => {
            const item = document.querySelector(sel);
            return item && `${item.dataset.start}/${item.dataset.end}` !== before;
        }""",
        arg=[ITEM_SELECTOR, before],
        timeout=5000,
    )


def test_the_right_edge_follows_the_pointer(editor):
    page = editor
    before = _item_box(page)

    _drag_handle(page, "right", DRAG_PIXELS)

    after = _item_box(page)
    moved = after["x"] + after["width"] - (before["x"] + before["width"])
    assert moved == pytest.approx(DRAG_PIXELS, abs=TOLERANCE_PIXELS), (
        f"the pointer moved {DRAG_PIXELS}px but the item's right edge moved {moved:.1f}px"
    )
    assert after["x"] == pytest.approx(before["x"], abs=2), (
        "the left edge moved too - a right-handle drag should only change the end"
    )


def test_the_left_edge_follows_the_pointer(editor):
    page = editor
    before = _item_box(page)

    _drag_handle(page, "left", DRAG_PIXELS)

    after = _item_box(page)
    moved = after["x"] - before["x"]
    assert moved == pytest.approx(DRAG_PIXELS, abs=TOLERANCE_PIXELS), (
        f"the pointer moved {DRAG_PIXELS}px but the item's left edge moved {moved:.1f}px"
    )
    right_before = before["x"] + before["width"]
    right_after = after["x"] + after["width"]
    assert right_after == pytest.approx(right_before, abs=2), (
        "the right edge moved too - a left-handle drag should only change the start"
    )
