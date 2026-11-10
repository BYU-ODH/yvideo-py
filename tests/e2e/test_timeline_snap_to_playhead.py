"""Dragging an item, or one of its handles, snaps the dragged edge to the playhead (#443).

The workflow: park the playhead on the exact frame (`,` / `.`), then drag an edge to it. While
dragging, the player previews the frame under the dragged edge and a dashed "shadow" scrubber
follows it, but the playhead itself stays parked. On release the player goes back to the parked
time, whether or not the edge snapped.

Distances here are in pixels, measured against the annotations container (an item's left and width
are percentages of it), so nothing depends on the viewport or the seeded durations beyond the
item existing.
"""

import pytest

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.django_db(transaction=True),
]

# Seeded by core/dev_seed.py on the demo content's first track.
ITEM_SELECTOR = '.track-item[data-annotation-type="mute"]'
VIDEO = "document.querySelector('.annotation-player-container video')"
# Inside timeline-snap.js's SNAP_ACQUIRE_PX (8), with room for sub-pixel layout.
NEAR_PIXELS = 4
# Well clear of SNAP_RELEASE_PX (14).
FAR_PIXELS = 60
# Far enough from the item that parking the playhead does not make anything snap by accident.
PARK_OFFSET_PIXELS = 40


@pytest.fixture
def editor(open_editor, page):
    page.set_viewport_size({"width": 1400, "height": 900})
    open_editor()
    page.wait_for_selector(ITEM_SELECTOR)
    return page


def _seconds_per_pixel(page):
    return page.evaluate(
        f"""() => {{
            const container = document.querySelector('.track-row-annotations-container');
            return {VIDEO}.duration / container.getBoundingClientRect().width;
        }}"""
    )


def _item_times(page):
    return page.eval_on_selector(
        ITEM_SELECTOR,
        """el => ({
            start: parseFloat(el.dataset.start),
            end: parseFloat(el.dataset.end),
            raw: `${el.dataset.start}/${el.dataset.end}`,
        })""",
    )


def _current_time(page):
    return page.evaluate(f"() => {VIDEO}.currentTime")


def _park_playhead(page, time):
    page.evaluate(
        f"""(time) => new Promise((resolve) => {{
            const video = {VIDEO};
            video.addEventListener('seeked', resolve, {{once: true}});
            video.currentTime = time;
        }})""",
        time,
    )
    # The real time after the seek, which is what the editor snaps to.
    return _current_time(page)


def _scrubber_transform(page, selector):
    return page.eval_on_selector(selector, "el => el.style.transform")


def _wait_for_save(page, before):
    page.wait_for_function(
        """([sel, before]) => {
            const item = document.querySelector(sel);
            return item && `${item.dataset.start}/${item.dataset.end}` !== before;
        }""",
        arg=[ITEM_SELECTOR, before["raw"]],
        timeout=5000,
    )


def _press_at(page, locator, x_within=None):
    box = locator.bounding_box()
    x = box["x"] + (box["width"] / 2 if x_within is None else x_within)
    y = box["y"] + box["height"] / 2
    page.mouse.move(x, y)
    page.mouse.down()
    return x, y


def test_a_handle_dragged_near_the_playhead_snaps_to_it(editor):
    page = editor
    before = _item_times(page)
    spp = _seconds_per_pixel(page)
    parked = _park_playhead(page, before["end"] + PARK_OFFSET_PIXELS * spp)

    x, y = _press_at(page, page.locator(ITEM_SELECTOR).locator(".resize-handle-right"))
    page.mouse.move(x + PARK_OFFSET_PIXELS - NEAR_PIXELS, y, steps=8)
    page.mouse.up()
    _wait_for_save(page, before)

    after = _item_times(page)
    assert after["end"] == pytest.approx(parked, abs=0.01), (
        f"the end landed at {after['end']:.3f}s, not on the playhead at {parked:.3f}s"
    )
    assert after["start"] == pytest.approx(before["start"], abs=0.01)
    assert _current_time(page) == pytest.approx(parked, abs=0.05)


def test_a_handle_dragged_away_from_the_playhead_does_not_snap_and_the_playhead_returns(
    editor,
):
    page = editor
    before = _item_times(page)
    spp = _seconds_per_pixel(page)
    parked = _park_playhead(page, before["end"] + PARK_OFFSET_PIXELS * spp)

    x, y = _press_at(page, page.locator(ITEM_SELECTOR).locator(".resize-handle-right"))
    page.mouse.move(x + PARK_OFFSET_PIXELS + FAR_PIXELS, y, steps=8)
    page.mouse.up()
    _wait_for_save(page, before)

    after = _item_times(page)
    assert after["end"] == pytest.approx(
        before["end"] + (PARK_OFFSET_PIXELS + FAR_PIXELS) * spp, abs=6 * spp
    )
    assert _current_time(page) == pytest.approx(parked, abs=0.05), (
        "the player should go back to the parked time after the drag"
    )


def test_while_dragging_the_player_previews_the_edge_and_the_playhead_stays_parked(
    editor,
):
    page = editor
    before = _item_times(page)
    spp = _seconds_per_pixel(page)
    parked = _park_playhead(page, before["end"] + PARK_OFFSET_PIXELS * spp)
    page.wait_for_timeout(100)
    playhead_before = _scrubber_transform(page, "#editor-scrubber")

    x, y = _press_at(page, page.locator(ITEM_SELECTOR).locator(".resize-handle-right"))
    page.mouse.move(x + PARK_OFFSET_PIXELS + FAR_PIXELS, y, steps=8)
    page.wait_for_timeout(200)

    shadow = page.locator("#editor-shadow-scrubber")
    assert "visible" in shadow.get_attribute("class")
    assert "snapped" not in shadow.get_attribute("class")
    assert _current_time(page) == pytest.approx(
        before["end"] + (PARK_OFFSET_PIXELS + FAR_PIXELS) * spp, abs=6 * spp
    ), "the player should show the frame under the dragged edge"
    assert _scrubber_transform(page, "#editor-scrubber") == playhead_before, (
        "the playhead moved during the drag"
    )

    # Back within reach of the playhead: the shadow line snaps onto it.
    page.mouse.move(x + PARK_OFFSET_PIXELS - NEAR_PIXELS, y, steps=8)
    page.wait_for_timeout(100)
    assert "snapped" in shadow.get_attribute("class")
    assert _current_time(page) == pytest.approx(parked, abs=0.05)

    page.mouse.up()
    _wait_for_save(page, before)
    assert "visible" not in (shadow.get_attribute("class") or "")


def test_a_moved_item_snaps_its_start_to_the_playhead(editor):
    page = editor
    before = _item_times(page)
    spp = _seconds_per_pixel(page)
    parked = _park_playhead(page, before["start"] + PARK_OFFSET_PIXELS * spp)

    content = page.locator(ITEM_SELECTOR).locator(".track-item-content")
    x, y = _press_at(page, content, x_within=5)
    page.mouse.move(x + PARK_OFFSET_PIXELS - NEAR_PIXELS, y, steps=12)
    page.wait_for_timeout(100)
    page.mouse.up()
    _wait_for_save(page, before)

    after = _item_times(page)
    assert after["start"] == pytest.approx(parked, abs=0.01), (
        f"the start landed at {after['start']:.3f}s, not on the playhead at {parked:.3f}s"
    )
    assert after["end"] - after["start"] == pytest.approx(
        before["end"] - before["start"], abs=0.01
    ), "a move should keep the item's length"
    assert _current_time(page) == pytest.approx(parked, abs=0.05)
