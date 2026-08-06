"""Direct manipulation of blur regions in the editor (issue #322).

None of this worked before Phase 4. `.annotation-box .blur { pointer-events: none }` matched the
player's blur div, and `.annotation-box-blur-editor { pointer-events: auto }` applied to the *box*
rather than its descendants, so every drag and resize handler was unreachable and every click fell
through to the overlay and created or retimed a point. These tests drive the real mouse against
the real seeded blurs, so they fail on any regression that puts that blocker back.

The headline case is `test_dragging_at_a_new_time_adds_a_point_and_leaves_the_others_alone`: a drag
must never be interpreted as "retime whichever point was last selected". That is doubly true now
that the rendered box is usually a tween between two points, so no single stored row owns it.
"""

from playwright.sync_api import expect
import pytest

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.django_db(transaction=True),
]

# Seeded by core/dev_seed.py. The watermark is the one-point (stationary) path; the flight path is
# the multi-point (moving) path, and its three points are deliberately asymmetric on every axis.
STATIONARY_BLUR = "Bird Watermark"
MOVING_BLUR = "Bird Flight Path"


def _open_editor(page, live_server):
    from core.models import Content

    content = Content.objects.get(title="Birds Overview")
    page.goto(f"{live_server.url}/login/dev/quick/")
    page.goto(f"{live_server.url}/video-editor/{content.pk}/")
    page.wait_for_function(
        """() => {
            const video = document.querySelector('.annotation-player-container video');
            const ticks = document.getElementById('tick-marks-container');
            return window.videoPlayer && video && !isNaN(video.duration) && video.duration > 0
                && ticks && ticks.children.length > 0;
        }""",
        timeout=10000,
    )
    return content


def _item_selector(annotation):
    # data-annotation-id is only unique per type - every annotation type numbers from 1.
    return f'.track-item[data-annotation-type="blur"][data-annotation-id="{annotation.pk}"]'


def _blur(name):
    from core.models import BlurAnnotation

    return BlurAnnotation.objects.get(name=name)


def _stored_points(name):
    """Round-tripped through the DB, because that is the only state that survives a reload."""
    return [
        (
            round(p.time, 2),
            round(p.x, 2),
            round(p.y, 2),
            round(p.width, 2),
            round(p.height, 2),
        )
        for p in _blur(name).positions.all()
    ]


def _select(page, name):
    """Click the blur's timeline item, which loads its form and hands it to BlurEditor."""
    annotation = _blur(name)
    item = page.locator(_item_selector(annotation))
    item.locator(".track-item-content").click()
    expect(page.locator("#blur-positions-wrapper")).to_be_visible()
    page.wait_for_selector("#blur-edit-rig", state="attached")
    return annotation


def _seek(page, seconds):
    page.evaluate("(t) => window.videoPlayer.setCurrentTime(t)", seconds)
    page.wait_for_function(
        "(t) => Math.abs(document.querySelector('.annotation-player-container video')"
        ".currentTime - t) < 0.2",
        arg=seconds,
        timeout=5000,
    )
    # One frame for the seeked handler to move the rig onto the new geometry.
    page.wait_for_timeout(150)


def _frame_box(page):
    """The annotation overlay's on-screen box, which Phase 1 made exactly the visible picture."""
    return page.locator(".annotation-box").bounding_box()


def _rig_percent(page):
    """The rig's geometry as percentages of the frame - the same space the server stores."""
    return page.evaluate(
        """() => {
            const box = document.querySelector('.annotation-box').getBoundingClientRect();
            const rig = document.querySelector('#blur-edit-rig').getBoundingClientRect();
            return {
                x: ((rig.x - box.x) / box.width) * 100,
                y: ((rig.y - box.y) / box.height) * 100,
                width: (rig.width / box.width) * 100,
                height: (rig.height / box.height) * 100,
            };
        }"""
    )


def _mark_panel(page):
    """Tag the points panel so its replacement can be detected.

    A save replaces #blur-positions-wrapper wholesale, so waiting for the tag to disappear is a
    signal that the response landed - unlike the number of rows, which usually does not change.
    Reading the database before that point is also a real race, not just a slow assertion: the
    live server holds a write transaction on the same SQLite file the test would be reading.
    """
    page.evaluate(
        "() => document.getElementById('blur-positions-wrapper').dataset.staleMarker = '1'"
    )


def _wait_for_save(page):
    page.wait_for_function(
        """() => {
            const wrapper = document.getElementById('blur-positions-wrapper');
            return wrapper && !wrapper.dataset.staleMarker;
        }""",
        timeout=5000,
    )


def _drag(page, from_percent, to_percent, steps=8):
    """Drag inside the video frame, in frame percentages."""
    frame = _frame_box(page)
    _mark_panel(page)

    def point(percent):
        return (
            frame["x"] + frame["width"] * percent[0] / 100,
            frame["y"] + frame["height"] * percent[1] / 100,
        )

    start, end = point(from_percent), point(to_percent)
    page.mouse.move(*start)
    page.mouse.down()
    page.mouse.move(*end, steps=steps)
    page.mouse.up()


def _drag_item_right(page, annotation, seconds):
    """Drag a timeline item later by roughly `seconds`, and wait for the save to land."""
    selector = _item_selector(annotation)
    duration = page.evaluate(
        "() => document.querySelector('.annotation-player-container video').duration"
    )
    item = page.locator(selector)
    target = page.locator(".track-row-annotations-container").first
    item_box = item.bounding_box()
    target_box = target.bounding_box()
    started_at = float(item.get_attribute("data-start"))
    item.locator(".track-item-content").drag_to(
        target,
        source_position={"x": 5, "y": item_box["height"] / 2},
        target_position={
            "x": item_box["x"]
            - target_box["x"]
            + 5
            + seconds / duration * target_box["width"],
            "y": item_box["y"] - target_box["y"] + item_box["height"] / 2,
        },
    )
    # Wait on the item bar, not a fixed delay: a move is two sequential requests (the update, then
    # the reloaded form), and its data-start is rewritten from the server's response. This timing
    # out means the drop was rejected -- it is how the duplicate-id bug above showed itself.
    page.wait_for_function(
        """(args) => {
            const item = document.querySelector(args.selector);
            return item && Math.abs(parseFloat(item.dataset.start) - args.was) > 0.2;
        }""",
        arg={"selector": selector, "was": started_at},
        timeout=5000,
    )


def _saved_points(page, name, expected_count):
    _wait_for_save(page)
    points = _stored_points(name)
    assert len(points) == expected_count, points
    return points


# --- creating and moving a region -------------------------------------------


def test_dragging_on_the_frame_frames_a_new_region(page, live_server, seeded_demo_data):
    """The gesture every screenshot tool has trained users on, and #322's first checklist item."""
    _open_editor(page, live_server)
    _select(page, STATIONARY_BLUR)
    _seek(page, _blur(STATIONARY_BLUR).start_time)

    _drag(page, (20, 25), (50, 60))

    time, x, y, width, height = _saved_points(page, STATIONARY_BLUR, 1)[0]
    assert (x, y) == pytest.approx((20, 25), abs=2)
    assert (width, height) == pytest.approx((30, 35), abs=2)
    assert time == pytest.approx(_blur(STATIONARY_BLUR).start_time, abs=0.01)


def test_a_plain_click_drops_a_usable_box_rather_than_a_sliver(
    page, live_server, seeded_demo_data
):
    """Nothing is lost for a user who never discovers dragging."""
    _open_editor(page, live_server)
    _select(page, STATIONARY_BLUR)
    _seek(page, _blur(STATIONARY_BLUR).start_time)

    frame = _frame_box(page)
    _mark_panel(page)
    page.mouse.click(
        frame["x"] + frame["width"] * 0.3, frame["y"] + frame["height"] * 0.4
    )

    _, x, y, width, height = _saved_points(page, STATIONARY_BLUR, 1)[0]
    assert width > 10 and height > 10, "a click should not leave a box too small to see"
    # Centered on the click, not anchored at it.
    assert x + width / 2 == pytest.approx(30, abs=2)
    assert y + height / 2 == pytest.approx(40, abs=2)


def test_the_rig_survives_the_response_it_triggered(
    page, live_server, seeded_demo_data
):
    """The box must not snap back once the save lands and the panel HTML is replaced."""
    _open_editor(page, live_server)
    _select(page, STATIONARY_BLUR)
    _seek(page, _blur(STATIONARY_BLUR).start_time)

    _drag(page, (55, 20), (80, 45))
    _saved_points(page, STATIONARY_BLUR, 1)
    page.wait_for_timeout(300)

    after = _rig_percent(page)
    assert after["x"] == pytest.approx(55, abs=2)
    assert after["y"] == pytest.approx(20, abs=2)
    assert after["width"] == pytest.approx(25, abs=2)


def test_a_corner_handle_resizes_from_the_opposite_corner(
    page, live_server, seeded_demo_data
):
    """Proves the pointer-events blocker is gone: this handler could not fire at all before."""
    _open_editor(page, live_server)
    _select(page, STATIONARY_BLUR)
    _seek(page, _blur(STATIONARY_BLUR).start_time)
    _drag(page, (20, 20), (50, 50))
    _saved_points(page, STATIONARY_BLUR, 1)
    page.wait_for_timeout(300)

    # The bottom-right handle: dragging it must change the size, never the origin.
    frame = _frame_box(page)
    handle = page.locator("#blur-edit-rig .blur-rig-handle").last
    handle_box = handle.bounding_box()
    _mark_panel(page)
    page.mouse.move(
        handle_box["x"] + handle_box["width"] / 2,
        handle_box["y"] + handle_box["height"] / 2,
    )
    page.mouse.down()
    page.mouse.move(
        frame["x"] + frame["width"] * 0.7, frame["y"] + frame["height"] * 0.65, steps=8
    )
    page.mouse.up()

    _, x, y, width, height = _saved_points(page, STATIONARY_BLUR, 1)[0]
    assert (x, y) == pytest.approx((20, 20), abs=2), "the anchored corner moved"
    assert width == pytest.approx(50, abs=3)
    assert height == pytest.approx(45, abs=3)


def test_dragging_inside_the_box_moves_it_without_resizing_it(
    page, live_server, seeded_demo_data
):
    """Distinguishes a move from a draw, which is what proves the rig itself got the pointer.

    If anything re-blocks pointer events on the rig, the gesture falls through to the overlay and
    becomes a *draw* instead - which still writes a plausible-looking box, so only the size tells
    the two apart. A move preserves it; a draw takes it from the drag span.
    """
    _open_editor(page, live_server)
    _select(page, MOVING_BLUR)
    _seek(page, 7.0)
    before = _rig_percent(page)

    # Grab the middle of the box and shove it down-right by a tenth of the frame.
    _drag(
        page,
        (before["x"] + before["width"] / 2, before["y"] + before["height"] / 2),
        (
            before["x"] + before["width"] / 2 + 10,
            before["y"] + before["height"] / 2 + 10,
        ),
    )

    _, x, y, width, height = _saved_points(page, MOVING_BLUR, 3)[1]
    assert (width, height) == pytest.approx((26.0, 17.0), abs=0.6), (
        "the box was resized, not moved"
    )
    assert x == pytest.approx(50.0, abs=2)
    assert y == pytest.approx(32.0, abs=2)


def test_a_region_cannot_be_dragged_off_the_frame(page, live_server, seeded_demo_data):
    _open_editor(page, live_server)
    _select(page, STATIONARY_BLUR)
    _seek(page, _blur(STATIONARY_BLUR).start_time)

    # Aim far past the bottom-right corner; the pointer is clamped to the frame.
    _drag(page, (85, 85), (140, 140))

    _, x, y, width, height = _saved_points(page, STATIONARY_BLUR, 1)[0]
    assert 0 <= x and x + width <= 100.01, f"escaped horizontally: {x} + {width}"
    assert 0 <= y and y + height <= 100.01, f"escaped vertically: {y} + {height}"


# --- the auto-keyframe rule --------------------------------------------------


def test_dragging_at_a_new_time_adds_a_point_and_leaves_the_others_alone(
    page, live_server, seeded_demo_data
):
    """The bug this phase exists to kill.

    The old handler sent the selected position's id together with the current playhead, so moving
    the box at a time between two points silently retimed one of them - losing coverage at the
    time it used to apply to, which for a blur means exposing what it was covering.
    """
    _open_editor(page, live_server)
    _select(page, MOVING_BLUR)
    before = _stored_points(MOVING_BLUR)
    assert [point[0] for point in before] == [3.0, 7.0, 11.0]

    _seek(page, 5.0)
    _drag(page, (60, 60), (80, 80))

    after = _saved_points(page, MOVING_BLUR, 4)
    assert [point[0] for point in after] == [3.0, 5.0, 7.0, 11.0]
    # Every pre-existing point is byte-for-byte untouched.
    assert [point for point in after if point[0] != 5.0] == before

    added = next(point for point in after if point[0] == 5.0)
    assert added[1] == pytest.approx(60, abs=2)
    assert added[2] == pytest.approx(60, abs=2)


def test_dragging_at_an_existing_point_updates_it_without_adding_one(
    page, live_server, seeded_demo_data
):
    _open_editor(page, live_server)
    _select(page, MOVING_BLUR)
    _seek(page, 7.0)

    _drag(page, (30, 65), (55, 85))

    after = _saved_points(page, MOVING_BLUR, 3)
    assert [point[0] for point in after] == [3.0, 7.0, 11.0], (
        "a duplicate point was added"
    )
    _, x, y, width, height = next(point for point in after if point[0] == 7.0)
    assert (x, y) == pytest.approx((30, 65), abs=2)
    assert (width, height) == pytest.approx((25, 20), abs=2)


def test_the_rig_shows_the_interpolated_box_between_points(
    page, live_server, seeded_demo_data
):
    """The rig has to sit on the blur it is editing, or the user drags the wrong thing.

    At t=5.0, halfway between the seeded points at t=3.0 and t=7.0, that means the tween.
    """
    _open_editor(page, live_server)
    _select(page, MOVING_BLUR)
    _seek(page, 5.0)

    rig = _rig_percent(page)
    assert rig["x"] == pytest.approx(26.25, abs=0.6)
    assert rig["y"] == pytest.approx(26.0, abs=0.6)
    assert rig["width"] == pytest.approx(24.0, abs=0.6)
    assert rig["height"] == pytest.approx(15.5, abs=0.6)


def test_the_rig_hides_outside_the_blurs_time_range_and_comes_back(
    page, live_server, seeded_demo_data
):
    """The player destroys and rebuilds its blur div around the window; the rig must not care.

    An editable box at a time when the blur does not exist would also invite an edit that cannot
    be stored, so it is hidden rather than left showing stale geometry.
    """
    _open_editor(page, live_server)
    _select(page, MOVING_BLUR)
    _seek(page, 5.0)
    expect(page.locator("#blur-edit-rig")).to_be_visible()

    _seek(page, 20.0)
    expect(page.locator("#blur-edit-rig")).to_be_hidden()

    _seek(page, 9.0)
    expect(page.locator("#blur-edit-rig")).to_be_visible()
    rig = _rig_percent(page)
    assert rig["x"] == pytest.approx(53.25, abs=0.6)


def test_escape_abandons_a_drag_without_saving(page, live_server, seeded_demo_data):
    _open_editor(page, live_server)
    _select(page, MOVING_BLUR)
    _seek(page, 7.0)
    before = _stored_points(MOVING_BLUR)

    frame = _frame_box(page)
    page.mouse.move(
        frame["x"] + frame["width"] * 0.7, frame["y"] + frame["height"] * 0.7
    )
    page.mouse.down()
    page.mouse.move(
        frame["x"] + frame["width"] * 0.9, frame["y"] + frame["height"] * 0.9, steps=6
    )
    page.keyboard.press("Escape")
    page.mouse.up()

    page.wait_for_timeout(400)
    assert _stored_points(MOVING_BLUR) == before
    # And the box is back on the stored geometry rather than left where the pointer was.
    assert _rig_percent(page)["x"] == pytest.approx(40.0, abs=1)


# --- the points panel and the timeline dots ---------------------------------


def test_deleting_a_point_updates_the_frame_without_a_reload(
    page, live_server, seeded_demo_data
):
    _open_editor(page, live_server)
    _select(page, MOVING_BLUR)
    _seek(page, 5.0)

    # Row 2 is the point at t=7.0; the first row has no delete button by design.
    row = page.locator("#blur-positions-wrapper .position-entry").nth(1)
    row.click()
    _mark_panel(page)
    row.locator(".blur-position-delete-button").click()

    assert [point[0] for point in _saved_points(page, MOVING_BLUR, 2)] == [3.0, 11.0]

    # The rig re-interpolates across the widened gap, with no page reload: selecting the row
    # seeked to t=7.0, which is now half way between the two surviving points rather than sitting
    # on one of them. Before the delete the box was at x=40 here; now it is the 3.0-to-11.0 tween.
    page.wait_for_timeout(300)
    rig = _rig_percent(page)
    assert rig["x"] == pytest.approx(12.5 + (66.5 - 12.5) * 0.5, abs=1)


def test_the_first_point_cannot_be_deleted(page, live_server, seeded_demo_data):
    """It supplies the geometry the blur starts with, and a blur with none cannot render."""
    _open_editor(page, live_server)
    _select(page, MOVING_BLUR)

    first_row = page.locator("#blur-positions-wrapper .position-entry").first
    expect(first_row.locator(".blur-position-delete-button")).to_have_count(0)
    expect(first_row.locator(".position-time-input")).to_have_attribute("readonly", "")


def test_clicking_a_panel_row_seeks_to_that_point(page, live_server, seeded_demo_data):
    _open_editor(page, live_server)
    _select(page, MOVING_BLUR)

    page.locator("#blur-positions-wrapper .position-entry").nth(1).click()
    page.wait_for_timeout(200)
    assert page.evaluate(
        "() => document.querySelector('.annotation-player-container video').currentTime"
    ) == pytest.approx(7.0, abs=0.3)


def test_clicking_a_timeline_dot_seeks_to_that_point(
    page, live_server, seeded_demo_data
):
    """The dots are delegated from the timeline now, because the item HTML is replaced on save."""
    annotation = _open_editor(page, live_server) and _select(page, MOVING_BLUR)

    dots = page.locator(f"{_item_selector(annotation)} .blur-position-locator")
    # The first point's dot is deliberately omitted: it would sit on the item's resize handle.
    expect(dots).to_have_count(2)
    dots.last.click()
    page.wait_for_timeout(200)
    assert page.evaluate(
        "() => document.querySelector('.annotation-player-container video').currentTime"
    ) == pytest.approx(11.0, abs=0.3)


def test_timeline_dots_land_at_their_points_relative_offsets(
    page, live_server, seeded_demo_data
):
    """The `calc()` in the dot's `left` was missing its closing paren, so CSS dropped it and every
    dot stacked at the left edge of the bar."""
    annotation = _open_editor(page, live_server) and _select(page, MOVING_BLUR)

    item = page.locator(_item_selector(annotation))
    item_box = item.bounding_box()
    offsets = []
    for index in range(2):
        dot_box = item.locator(".blur-position-locator").nth(index).bounding_box()
        center = dot_box["x"] + dot_box["width"] / 2
        offsets.append((center - item_box["x"]) / item_box["width"])

    # Points at 7.0 and 11.0 within a 3.0-11.0 item: half way, and at the end.
    assert offsets[0] == pytest.approx(0.5, abs=0.03)
    assert offsets[1] == pytest.approx(1.0, abs=0.03)
    assert offsets[0] != pytest.approx(offsets[1], abs=0.05), "dots are stacked"


# --- the item bar: #322 items 2 and 3 ---------------------------------------
#
# core/tests/test_blur_position_reconcile_endpoint.py covers the same two behaviours against the
# update endpoint. What these add is the path the user actually takes to reach it - the resize
# handles and the timeline drag - plus the dots landing where the retimed points now are.


def test_dragging_the_left_handle_moves_the_first_point(
    page, live_server, seeded_demo_data
):
    """#322 item 2: the left handle sets the *start*, so the first point has to follow it.

    Before this, dragging the left handle changed the item's start while its first point stayed
    behind, leaving the blur's opening frames covered by geometry from somewhere else entirely.
    """
    annotation = _open_editor(page, live_server) and _select(page, MOVING_BLUR)
    assert [point[0] for point in _stored_points(MOVING_BLUR)] == [3.0, 7.0, 11.0]

    item = page.locator(_item_selector(annotation))
    item_box = item.bounding_box()
    handle = item.locator(".resize-handle-left")
    handle_box = handle.bounding_box()
    container = page.locator(".track-row-annotations-container").first.bounding_box()
    duration = page.evaluate(
        "() => document.querySelector('.annotation-player-container video').duration"
    )

    # Drag the left edge right by two seconds' worth of timeline.
    seconds_per_pixel = duration / container["width"]
    page.mouse.move(
        handle_box["x"] + handle_box["width"] / 2,
        handle_box["y"] + handle_box["height"] / 2,
    )
    page.mouse.down()
    page.mouse.move(
        handle_box["x"] + handle_box["width"] / 2 + 2.0 / seconds_per_pixel,
        handle_box["y"] + handle_box["height"] / 2,
        steps=10,
    )
    page.mouse.up()
    page.wait_for_timeout(800)

    blur = _blur(MOVING_BLUR)
    assert blur.start_time == pytest.approx(5.0, abs=0.4), (
        "the left handle did not set the start"
    )
    assert blur.end_time == pytest.approx(11.0, abs=0.2), (
        "the right edge should not have moved"
    )

    points = _stored_points(MOVING_BLUR)
    assert points[0][0] == pytest.approx(blur.start_time, abs=0.01)
    # The point that was at 3.0 is gone; the geometry showing at the new start is what survives,
    # so the blur looks the same at t=5.0 as it did before the resize.
    assert points[0][1] == pytest.approx(26.25, abs=3), (
        "the first point lost its geometry"
    )
    assert [round(point[0], 1) for point in points[1:]] == [7.0, 11.0]
    assert item_box  # the drag started from a laid-out bar, not a zero-size one


def test_the_player_overlay_does_not_share_an_id_with_the_timeline_item(
    page, live_server, seeded_demo_data
):
    """A duplicate id made dragging a blur item fail roughly one time in ten.

    The editor's timeline items are id="<type>-<id>", so naming the player's blur div "blur-<id>"
    put two elements on the page under one id. getElementById returned the overlay, which carries
    no data-annotation-id, and the item drag posted to /annotations/blur/undefined/update/. It
    looked random because it depended on whether the overlay was painted at that moment.
    """
    annotation = _open_editor(page, live_server) and _select(page, MOVING_BLUR)
    _seek(page, 5.0)

    found = page.evaluate(
        """(id) => {
            const sharingTheItemsId = document.querySelectorAll('[id="blur-' + id + '"]');
            return {
                overlayPresent: !!document.querySelector('#blur-overlay-' + id),
                sharingTheItemsId: [...sharingTheItemsId].map((el) => el.className),
                // What the drop handler does to find the item it was handed.
                resolvesTo: document.getElementById('blur-' + id)?.dataset.annotationId,
            };
        }""",
        annotation.pk,
    )
    assert found["overlayPresent"], "nothing was being overlaid, so nothing was proven"
    assert len(found["sharingTheItemsId"]) == 1, found["sharingTheItemsId"]
    assert found["resolvesTo"] == str(annotation.pk)


def test_dragging_the_item_shifts_every_point_and_its_dot(
    page, live_server, seeded_demo_data
):
    """#322 item 3, and the `calc()` fix: after a move the dots must still mark their points."""
    annotation = _open_editor(page, live_server) and _select(page, MOVING_BLUR)
    before = _stored_points(MOVING_BLUR)

    _drag_item_right(page, annotation, seconds=4.0)

    after = _stored_points(MOVING_BLUR)
    assert len(after) == len(before), "a move must not add or drop points"
    # The delta is read back from where the item actually landed rather than assumed: how far a
    # drop travels also depends on the timeline's own scroll geometry, and what #322 item 3 asks
    # for is that every point moves *with the item*, by one shared delta.
    delta = _blur(MOVING_BLUR).start_time - before[0][0]
    assert delta > 1.0, f"the item barely moved ({delta}s), so this proves nothing"
    for old, new in zip(before, after):
        assert new[0] == pytest.approx(old[0] + delta, abs=0.02), (
            "point did not shift with the item"
        )
        assert new[1:] == old[1:], "a move must not change any geometry"

    # And the dots are still at the same offsets within the bar, since the whole item moved.
    moved_item = page.locator(_item_selector(annotation))
    moved_box = moved_item.bounding_box()
    offsets = []
    for index in range(2):
        dot_box = moved_item.locator(".blur-position-locator").nth(index).bounding_box()
        offsets.append(
            (dot_box["x"] + dot_box["width"] / 2 - moved_box["x"]) / moved_box["width"]
        )
    assert offsets[0] == pytest.approx(0.5, abs=0.05)
    assert offsets[1] == pytest.approx(1.0, abs=0.05)


# --- creation-time rounding --------------------------------------------------


@pytest.mark.parametrize(
    "playhead",
    [
        7.0,  # a round playhead: the storage rounding is a no-op and this always worked
        7.3066666,  # rounds UP, so a naive start lands after the playhead that asked for it
        16.4499,
        3.5050,
    ],
)
def test_a_newly_created_blur_is_drawn_and_survives_its_first_drag(
    page, live_server, seeded_demo_data, playhead
):
    """Creating a blur at an arbitrary playhead, then dragging it, used to make it vanish.

    Annotations are stored to the hundredth of a second by BaseAnnotation.save(), which *rounds*.
    Asking for a start of 7.3066 therefore stored 7.31 - a few milliseconds after the playhead - so
    the annotation was not active yet and the player drew nothing. The editor's bar meanwhile
    carried the unrounded value it had asked for, so the rig appeared anyway, and then hid the
    instant a save replaced that bar with the server's own HTML.

    Parametrised over the playhead because that is the whole bug: every round number passes.
    """
    _open_editor(page, live_server)
    page.evaluate("(t) => window.videoPlayer.setCurrentTime(t)", playhead)
    page.wait_for_timeout(300)

    page.locator('.annotation-type-add-button[data-annotation-type="blur"]').click()
    page.wait_for_selector("#blur-edit-rig", timeout=5000)
    page.wait_for_timeout(400)

    created = page.locator(
        ".track-item[data-annotation-type=blur].active-track-item"
    ).get_attribute("data-start")
    current = page.evaluate(
        "() => document.querySelector('.annotation-player-container video').currentTime"
    )
    assert float(created) <= current + 1e-9, (
        f"the stored start ({created}) is after the playhead ({current}) that created it"
    )

    def state():
        return page.evaluate(
            """() => {
                const rig = document.querySelector('#blur-edit-rig');
                return {
                    visible: !!rig && !rig.hidden && rig.getBoundingClientRect().width > 0,
                    // The blurred box the player draws. The rig is only an outline, so the rig
                    // being present is not on its own evidence that anything is covered.
                    overlays: document.querySelectorAll('[id^=blur-overlay-]').length,
                };
            }"""
        )

    before = state()
    assert before["visible"], "the rig was not showing straight after creation"
    assert before["overlays"] >= 1, "the new blur was created but nothing was drawn"

    # Grab the middle of the default box and move it up and left.
    _drag(page, (50, 50), (25, 25))
    _wait_for_save(page)
    page.wait_for_timeout(300)

    after = state()
    assert after["visible"], "the rig vanished after its first drag"
    assert after["overlays"] == before["overlays"], (
        "the blurred box disappeared after the drag"
    )


# --- the overlay's place in the stack ----------------------------------------


def test_the_controls_do_not_cover_the_editable_frame(
    page, live_server, seeded_demo_data
):
    """The bottom of the picture has to be reachable, because that is where subtitles are.

    .video-controls is z-index 20 against the overlay's 10, so while the controls sat on top of the
    picture they took every pointer event in that strip: the rig's bottom handles could not be
    grabbed and a blur could not be placed over a burned-in caption. In the editor the controls are
    laid out below the picture instead. Raising the overlay above them would have been worse - it
    accepts pointer events while a blur is selected, so it would have swallowed the scrubber.
    """
    _open_editor(page, live_server)
    _select(page, MOVING_BLUR)
    _seek(page, 5.0)

    geometry = page.evaluate(
        """() => {
            const box = document.querySelector('.annotation-box').getBoundingClientRect();
            const controls = document.querySelector('.video-controls').getBoundingClientRect();
            return {
                overlap: Math.max(0, Math.min(box.bottom, controls.bottom)
                                     - Math.max(box.top, controls.top)),
                boxHeight: box.height,
                controlsHeight: controls.height,
                // What is actually on top at the very bottom edge of the picture.
                atBottomEdge: document.elementFromPoint(
                    box.x + box.width / 2, box.bottom - 2)?.className,
            };
        }"""
    )
    assert geometry["controlsHeight"] > 0, (
        "the controls were not rendered, so nothing was proven"
    )
    assert geometry["overlap"] == 0, (
        f"the controls overlap the editable picture by {geometry['overlap']}px"
    )

    # And a blur can be placed against the very bottom of the frame.
    _drag(page, (30, 88), (60, 99))
    added = next(
        point for point in _saved_points(page, MOVING_BLUR, 4) if point[0] == 5.0
    )
    _, _, y, _, height = added
    assert y + height > 95, f"could not reach the bottom of the frame: {y} + {height}"


def test_the_scrubber_is_still_clickable_while_a_blur_is_selected(
    page, live_server, seeded_demo_data
):
    """Scrubbing between points is the entire multi-point workflow, so it cannot be blocked."""
    _open_editor(page, live_server)
    _select(page, MOVING_BLUR)
    _seek(page, 4.0)

    scrubber = page.locator(".video-wrapper .scrubber").first
    box = scrubber.bounding_box()
    page.mouse.click(box["x"] + box["width"] * 0.5, box["y"] + box["height"] / 2)
    page.wait_for_timeout(400)

    moved = page.evaluate(
        "() => document.querySelector('.annotation-player-container video').currentTime"
    )
    assert moved > 6.0, f"clicking the scrubber did not seek (still at {moved})"


def test_a_blur_is_drawn_behind_a_comment(page, live_server, seeded_demo_data):
    """A comment is text the viewer has to read; a blur exists to conceal. Order matters.

    Both are position:absolute inside the overlay, so before this was stated explicitly the
    winner was whichever annotation applyAnnotations happened to append last.
    """
    _open_editor(page, live_server)
    # Bird Notes 1 (8.0-15.0) and Bird Flight Path (3.0-11.0) are both live at t=9.
    _select(page, MOVING_BLUR)
    _seek(page, 9.0)

    stacking = page.evaluate(
        """() => {
            const blur = document.querySelector('.annotation-box .blur-position');
            const comment = document.querySelector('.annotation-box .comment-text-box');
            if (!blur || !comment) return null;
            return {
                blur: +getComputedStyle(blur).zIndex,
                comment: +getComputedStyle(comment).zIndex,
            };
        }"""
    )
    assert stacking is not None, "needed a blur and a comment on screen at once"
    assert stacking["blur"] < stacking["comment"], stacking


def test_deleting_a_blur_removes_it_from_the_player(
    page, live_server, seeded_demo_data
):
    """It used to stay on screen until a reload, concealing video with nothing to select.

    applyAnnotations only cleans up an element while iterating the annotation it belongs to, so a
    deleted annotation is never reached and its box is orphaned.
    """
    _open_editor(page, live_server)
    annotation = _select(page, MOVING_BLUR)
    _seek(page, 5.0)
    assert page.locator(f"#blur-overlay-{annotation.pk}").count() == 1

    page.locator("#annotation-form-delete-button").click()
    page.wait_for_timeout(1200)

    from core.models import BlurAnnotation

    assert not BlurAnnotation.objects.filter(pk=annotation.pk, active=True).exists()
    assert page.locator(f"#blur-overlay-{annotation.pk}").count() == 0, (
        "the blurred box outlived the annotation it was drawn for"
    )
    expect(page.locator("#blur-edit-rig")).to_have_count(0)


def test_the_editor_layout_survives_fullscreen(page, live_server, seeded_demo_data):
    """Entering fullscreen re-lays out the player, and the controls must stay clear of the picture.

    Issue #322 calls out the normal-to-fullscreen transition specifically, and this is the layout
    that transition has to hold: a flexed video with the controls in flow beneath it.
    """
    # Larger than the 1280-wide source, so "fullscreen enlarged the picture" below is a real check
    # rather than one the default viewport makes impossible.
    page.set_viewport_size({"width": 1600, "height": 1000})
    _open_editor(page, live_server)
    annotation = _select(page, MOVING_BLUR)
    _seek(page, 5.0)
    before = _rig_percent(page)

    page.locator(".fullscreen-btn").click()
    page.wait_for_function("() => !!document.fullscreenElement", timeout=3000)
    page.wait_for_timeout(500)

    measured = page.evaluate(
        """() => {
            const box = document.querySelector('.annotation-box').getBoundingClientRect();
            const controls = document.querySelector('.video-controls').getBoundingClientRect();
            const video = document.querySelector('.annotation-player-container video');
            return {
                overlap: Math.max(0, Math.min(box.bottom, controls.bottom)
                                     - Math.max(box.top, controls.top)),
                pictureRatio: box.width / box.height,
                intrinsicRatio: video.videoWidth / video.videoHeight,
                grewBigger: box.width,
            };
        }"""
    )
    assert measured["overlap"] == 0, (
        f"controls overlap by {measured['overlap']}px in fullscreen"
    )
    assert measured["pictureRatio"] == pytest.approx(
        measured["intrinsicRatio"], abs=0.01
    ), "the picture was distorted by the fullscreen transition"
    assert measured["grewBigger"] > 1280, (
        "fullscreen did not actually enlarge the picture"
    )

    # The blur is still over the same part of the picture, which is the whole point of storing
    # percentages of the frame rather than pixels.
    after = _rig_percent(page)
    for field in ("x", "y", "width", "height"):
        assert after[field] == pytest.approx(before[field], abs=0.5), field
    assert annotation.pk


@pytest.mark.parametrize("playhead", [7.0, 7.02, 6.98])
def test_moving_a_point_repeatedly_never_changes_its_size(
    page, live_server, seeded_demo_data, playhead
):
    """A move must be exactly a move, however many times it is repeated.

    Parked a few milliseconds off a stored point, the rig correctly shows the *interpolated* rect
    for that moment - a hair along the way toward the next point. Committing that rect while the
    server snapped the write onto the point filed it as the geometry at the point's own time,
    nudging it toward its neighbour. The next drag started from the nudged value and went further,
    so width and height crept on every single release, growing or shrinking depending on which way
    the neighbouring point lay. The off-by-a-frame playheads here are the whole test: 7.0 exactly
    always passed.
    """
    _open_editor(page, live_server)
    _select(page, MOVING_BLUR)
    _seek(page, playhead)

    def point():
        return next(p for p in _stored_points(MOVING_BLUR) if abs(p[0] - 7.0) < 0.06)

    original = point()
    assert original[3:] == (26.0, 17.0), original

    for step in range(4):
        rig = _rig_percent(page)
        centre = (rig["x"] + rig["width"] / 2, rig["y"] + rig["height"] / 2)
        _drag(page, centre, (centre[0] + 3, centre[1]))
        _wait_for_save(page)
        page.wait_for_timeout(150)

        moved = point()
        assert moved[3:] == original[3:], (
            f"size drifted from {original[3:]} to {moved[3:]} after {step + 1} move(s)"
        )
        assert moved[2] == original[2], f"y drifted from {original[2]} to {moved[2]}"
        assert moved[1] > original[1], (
            "the box did not actually move, so nothing was proven"
        )

    # And it really did travel: four nudges of ~3% each.
    assert point()[1] == pytest.approx(original[1] + 12, abs=1.5)


def _highlighted(page):
    """The point ids highlighted in the panel and on the timeline, as a pair of lists."""
    return page.evaluate(
        """() => ({
            rows: [...document.querySelectorAll('.position-entry.active-position-entry')]
                .map((r) => r.dataset.positionId),
            dots: [...document.querySelectorAll(
                '.blur-position-locator.active-blur-position-locator')]
                .map((d) => d.dataset.positionId),
        })"""
    )


def test_selecting_a_dot_highlights_its_panel_row(page, live_server, seeded_demo_data):
    """The two views of a point have to agree, whichever one is clicked.

    Clicking a row highlighted its dot, but not the reverse: #timeline-wrapper is an *ancestor* of
    the track items, so the delegated dot handler ran only after the item's own click handler had
    already begun reloading the detail form - and the new rows came back with no highlight on them.
    """
    annotation = _open_editor(page, live_server) and _select(page, MOVING_BLUR)
    dot = page.locator(f"{_item_selector(annotation)} .blur-position-locator").first
    expected = dot.get_attribute("data-position-id")

    dot.click()
    page.wait_for_timeout(500)

    highlighted = _highlighted(page)
    assert highlighted["dots"] == [expected]
    assert highlighted["rows"] == [expected], "the dot's row was not highlighted"


def test_selecting_a_panel_row_highlights_its_dot(page, live_server, seeded_demo_data):
    """The direction that already worked, kept honest."""
    _open_editor(page, live_server)
    _select(page, MOVING_BLUR)

    row = page.locator("#blur-positions-wrapper .position-entry").nth(1)
    expected = row.get_attribute("data-position-id")
    row.click()
    page.wait_for_timeout(500)

    highlighted = _highlighted(page)
    assert highlighted["rows"] == [expected]
    assert highlighted["dots"] == [expected]


def test_clicking_a_dot_on_an_unselected_blur_selects_both_the_blur_and_the_point(
    page, live_server, seeded_demo_data
):
    """Selecting an item seeks to its start, so landing on the clicked point needs care."""
    from core.models import BlurAnnotation

    _open_editor(page, live_server)
    annotation = BlurAnnotation.objects.get(name=MOVING_BLUR)
    dot = page.locator(f"{_item_selector(annotation)} .blur-position-locator").first
    expected = dot.get_attribute("data-position-id")
    expected_time = float(dot.get_attribute("data-position-time"))

    dot.click()
    page.wait_for_selector("#blur-edit-rig", timeout=5000)
    page.wait_for_timeout(700)

    assert _highlighted(page)["rows"] == [expected]
    assert page.evaluate(
        "() => document.querySelector('.annotation-player-container video').currentTime"
    ) == pytest.approx(expected_time, abs=0.3), (
        "did not land on the point that was clicked"
    )


def test_scrubbing_onto_a_point_highlights_it(page, live_server, seeded_demo_data):
    """The highlight follows the playhead, not the last thing clicked.

    That is what makes it survive a form reload, and it also answers "which point am I editing?"
    while scrubbing - the question the panel exists to answer.
    """
    _open_editor(page, live_server)
    _select(page, MOVING_BLUR)

    _seek(page, 5.0)  # between points
    assert _highlighted(page)["rows"] == [], (
        "a tween is not a point and should highlight nothing"
    )

    _seek(page, 11.0)  # exactly on the last point
    on_point = _highlighted(page)
    assert len(on_point["rows"]) == 1
    assert on_point["rows"] == on_point["dots"]


def test_the_highlight_clears_when_the_blur_loses_focus(
    page, live_server, seeded_demo_data
):
    """A dot left lit on an unselected blur claims a point is being edited when none is.

    The panel rows vanish with the detail form, so the row half of the highlight cleaned itself up
    and hid this: the dots live in the track item, which outlives the selection.
    """
    annotation = _open_editor(page, live_server) and _select(page, MOVING_BLUR)
    page.locator(f"{_item_selector(annotation)} .blur-position-locator").first.click()
    page.wait_for_timeout(400)
    assert _highlighted(page)["dots"] != [], "nothing was lit, so nothing was proven"

    # Select something that is not a blur at all.
    page.locator(
        '.track-item[data-annotation-type="comment"] .track-item-content'
    ).first.click()
    page.wait_for_timeout(900)

    expect(page.locator("#blur-edit-rig")).to_have_count(0)
    assert _highlighted(page)["dots"] == [], (
        "a dot stayed lit on a blur that lost focus"
    )


def test_dots_hold_their_times_while_a_resize_handle_is_dragged(
    page, live_server, seeded_demo_data
):
    """A dot marks an absolute time, so stretching the bar must not drag it along.

    Its `left` is a percentage *of the item*, so while the item's width was in flux the dots slid
    with it and then snapped back to their real times when placeTrackItems ran on release. The
    assertions here are taken mid-drag, before the mouse is released - which is the only moment the
    bug existed.
    """
    annotation = _open_editor(page, live_server) and _select(page, MOVING_BLUR)
    selector = _item_selector(annotation)

    def measure():
        return page.evaluate(
            """(sel) => {
                const item = document.querySelector(sel);
                return {
                    barWidth: item.getBoundingClientRect().width,
                    centres: [...item.querySelectorAll('.blur-position-locator')].map((dot) => {
                        const box = dot.getBoundingClientRect();
                        return box.x + box.width / 2;
                    }),
                };
            }""",
            selector,
        )

    before = measure()
    handle = page.locator(f"{selector} .resize-handle-right").bounding_box()
    page.mouse.move(
        handle["x"] + handle["width"] / 2, handle["y"] + handle["height"] / 2
    )
    page.mouse.down()
    page.mouse.move(
        handle["x"] + handle["width"] / 2 + 140,
        handle["y"] + handle["height"] / 2,
        steps=6,
    )
    page.wait_for_timeout(200)
    during = measure()
    page.mouse.up()
    page.wait_for_timeout(900)
    after = measure()

    assert during["barWidth"] > before["barWidth"] + 50, (
        "the bar did not actually stretch, so this proves nothing"
    )
    for index, (was, now) in enumerate(zip(before["centres"], during["centres"])):
        assert now == pytest.approx(was, abs=3), (
            f"dot {index} slid from {was} to {now} while the bar was being stretched"
        )
    # And it does not jump once the real times come back from the server.
    for index, (mid, settled) in enumerate(zip(during["centres"], after["centres"])):
        assert settled == pytest.approx(mid, abs=3), f"dot {index} snapped on release"
