"""Direct manipulation of blur regions in the editor (issue #322).

None of this worked before Phase 4. `.annotation-box .blur { pointer-events: none }` matched the
player's blur div, and nothing re-enabled events for the box being edited, so every drag and resize
handler was unreachable and every click fell through to the overlay and created or retimed a point.
These tests drive the real mouse against the real seeded blurs, so they fail on any regression that
puts that blocker back.

The model under test: **a blur is one region that can move.** It arrives with its box already on the
frame, and the only thing a user ever does is move or resize that box. There is no gesture that
creates a region - see test_dragging_on_empty_frame_does_nothing, which asserts the absence of the
one that used to exist. Gestures that read as "add a blur" are what left users of the old system
unable to work out how to get two blurs on screen at once.

The headline case is `test_dragging_at_a_new_time_adds_a_point_and_leaves_the_others_alone`: moving
the box must never be interpreted as "retime whichever point was last selected". That is doubly true
now that the rendered box is usually a tween between two points, so no single stored row owns it.
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
    """Tag the rows of the points panel so their replacement can be detected.

    A save replaces #positions-list, so waiting for the tag to disappear is a signal that the
    response landed - unlike the number of rows, which usually does not change. Reading the database
    before that point is also a real race, not just a slow assertion: the live server holds a write
    transaction on the same SQLite file the test would be reading.

    The rows and not the whole wrapper, because the wrapper deliberately survives a save: the help
    text and the aria-live status line sit outside the rows so they are not destroyed and rebuilt
    on every edit.
    """
    page.evaluate(
        "() => document.getElementById('positions-list').dataset.staleMarker = '1'"
    )


def _wait_for_save(page):
    page.wait_for_function(
        """() => {
            const list = document.getElementById('positions-list');
            return list && !list.dataset.staleMarker;
        }""",
        timeout=5000,
    )


def _frame_point(page, percent):
    frame = _frame_box(page)
    return (
        frame["x"] + frame["width"] * percent[0] / 100,
        frame["y"] + frame["height"] * percent[1] / 100,
    )


def _drag_in_frame(page, from_percent, to_percent, steps=8):
    """Drag the mouse across the video frame, in frame percentages.

    Nothing on the frame answers this except the blur box itself, so a drag that starts on empty
    picture is inert by design - see test_dragging_on_empty_frame_does_nothing.
    """
    _mark_panel(page)
    page.mouse.move(*_frame_point(page, from_percent))
    page.mouse.down()
    page.mouse.move(*_frame_point(page, to_percent), steps=steps)
    page.mouse.up()


def _move_rig_to(page, x_percent, y_percent, steps=8):
    """Drag the blur box so its top-left corner lands at (x_percent, y_percent).

    Grabs the middle of the box, clear of the eight resize handles, and translates by the difference
    - which is what the rig's move gesture does, so the grip is preserved rather than the box being
    centred on the pointer.
    """
    rig = _rig_percent(page)
    half = (rig["width"] / 2, rig["height"] / 2)
    _drag_in_frame(
        page,
        (rig["x"] + half[0], rig["y"] + half[1]),
        (x_percent + half[0], y_percent + half[1]),
        steps=steps,
    )


def _drag_handle_to(page, handle, x_percent, y_percent, steps=8):
    """Drag one of the rig's eight resize handles to a point on the frame."""
    box = page.locator(
        f'#blur-edit-rig .blur-rig-handle[data-handle="{handle}"]'
    ).bounding_box()
    _mark_panel(page)
    page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
    page.mouse.down()
    page.mouse.move(*_frame_point(page, (x_percent, y_percent)), steps=steps)
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
    # Wait on the item bar, not a fixed delay: a move is two sequential requests (the update, then the
    # reloaded form), and its data-start is rewritten from the server's response. Timing out here
    # means the drop was rejected.
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


# --- moving and resizing the box --------------------------------------------
#
# There is nothing here that creates a blur region: a blur arrives with its box already on the frame
# (ensure_first_position), and every gesture moves or resizes that box.


def test_dragging_on_empty_frame_does_nothing(page, open_editor):
    """The frame is not a canvas. This is the removed create gesture, asserted absent.

    A drag on empty picture used to rubber-band a new rectangle over the existing box. Nothing
    should answer it now: not a new point, not a changed one, not a resized box.
    """
    open_editor()
    _select(page, MOVING_BLUR)
    _seek(page, 5.0)
    before = _stored_points(MOVING_BLUR)
    box_before = _rig_percent(page)

    # A long drag, then a plain click, both well clear of the box at t=5.0 (x 26, y 26, 24 x 16).
    _drag_in_frame(page, (70, 70), (90, 90))
    page.wait_for_timeout(500)
    page.mouse.click(*_frame_point(page, (80, 15)))
    page.wait_for_timeout(500)

    assert _stored_points(MOVING_BLUR) == before, "the frame accepted an edit"
    after = _rig_percent(page)
    assert after["x"] == pytest.approx(box_before["x"], abs=1)
    assert after["width"] == pytest.approx(box_before["width"], abs=1)


def test_the_rig_survives_the_response_it_triggered(page, open_editor):
    """The box must not snap back once the save lands and the panel rows are replaced."""
    open_editor()
    _select(page, STATIONARY_BLUR)
    _seek(page, _blur(STATIONARY_BLUR).start_time)

    _move_rig_to(page, 55, 20)
    _saved_points(page, STATIONARY_BLUR, 1)
    page.wait_for_timeout(300)

    after = _rig_percent(page)
    assert after["x"] == pytest.approx(55, abs=2)
    assert after["y"] == pytest.approx(20, abs=2)
    # Seeded 26 x 12: a move must not have changed the size on the way through the server.
    assert after["width"] == pytest.approx(26, abs=2)


def test_a_corner_handle_resizes_from_the_opposite_corner(page, open_editor):
    """Proves the pointer-events blocker is gone: this handler could not fire at all before."""
    open_editor()
    _select(page, STATIONARY_BLUR)
    _seek(page, _blur(STATIONARY_BLUR).start_time)
    _move_rig_to(page, 20, 20)
    _saved_points(page, STATIONARY_BLUR, 1)
    page.wait_for_timeout(300)

    # The bottom-right corner: dragging it must change the size, never the origin.
    _drag_handle_to(page, "se", 70, 65)

    _, x, y, width, height = _saved_points(page, STATIONARY_BLUR, 1)[0]
    assert (x, y) == pytest.approx((20, 20), abs=2), "the anchored corner moved"
    assert width == pytest.approx(50, abs=3)
    assert height == pytest.approx(45, abs=3)


@pytest.mark.parametrize(
    "handle,target,expected",
    [
        # Each edge handle moves one edge, and the other axis must come through untouched.
        ("e", (75, 90), {"x": 20, "y": 20, "width": 55, "height": 12}),
        ("w", (10, 5), {"x": 10, "y": 20, "width": 36, "height": 12}),
        ("s", (95, 60), {"x": 20, "y": 20, "width": 26, "height": 40}),
        ("n", (5, 10), {"x": 20, "y": 10, "width": 26, "height": 22}),
    ],
)
def test_an_edge_handle_resizes_one_axis_only(
    page, open_editor, handle, target, expected
):
    """Corners alone force a user to change both dimensions to correct one of them.

    The far coordinate passed to each drag is deliberately absurd - the pointer is taken well off
    the handle's axis - because the whole point of an edge handle is that the other axis ignores it.
    """
    open_editor()
    _select(page, STATIONARY_BLUR)
    _seek(page, _blur(STATIONARY_BLUR).start_time)
    _move_rig_to(page, 20, 20)
    _saved_points(page, STATIONARY_BLUR, 1)
    page.wait_for_timeout(300)

    _drag_handle_to(page, handle, *target)

    _, x, y, width, height = _saved_points(page, STATIONARY_BLUR, 1)[0]
    got = {"x": x, "y": y, "width": width, "height": height}
    for field, want in expected.items():
        assert got[field] == pytest.approx(want, abs=3), (
            f"{handle} handle: {field} is {got[field]}, expected {want} ({got})"
        )


def test_the_rig_has_a_handle_on_every_edge_and_corner(page, open_editor):
    """All eight, and each still big enough to grab.

    Three of the eight (nw, ne, sw) have no drag test, so this is their only coverage. The hit
    target is asserted because it is invisible: the handles paint smaller than they catch, via a
    transparent border, so shrinking them to the painted size would look correct in a screenshot
    and be unusable with a finger. How large the *paint* is, by contrast, is a design judgement
    about how much of the concealed subject a handle may cover - checked by eye, not pinned here.
    """
    open_editor()
    _select(page, MOVING_BLUR)
    _seek(page, 7.0)

    handles = page.locator("#blur-edit-rig .blur-rig-handle")
    expect(handles).to_have_count(8)
    assert sorted(handles.nth(i).get_attribute("data-handle") for i in range(8)) == [
        "e",
        "n",
        "ne",
        "nw",
        "s",
        "se",
        "sw",
        "w",
    ]

    clickable = page.evaluate(
        """() => document.querySelector('#blur-edit-rig .blur-rig-handle')
            .getBoundingClientRect().width"""
    )
    assert clickable >= 12, (
        f"only {clickable}px of hit target, which is too small to grab"
    )


def test_dragging_inside_the_box_moves_it_without_resizing_it(page, open_editor):
    """A move translates by the pointer's travel, so the user keeps the same grip on the box.

    Centring the box on the pointer instead would work for a grab in the middle and jump the box
    sideways for a grab anywhere else - so this grabs off-centre on purpose.
    """
    open_editor()
    _select(page, MOVING_BLUR)
    _seek(page, 7.0)
    before = _rig_percent(page)

    # A quarter of the way in from the top-left, then ten percent of the frame down and right.
    grab = (before["x"] + before["width"] / 4, before["y"] + before["height"] / 4)
    _drag_in_frame(page, grab, (grab[0] + 10, grab[1] + 10))

    _, x, y, width, height = _saved_points(page, MOVING_BLUR, 3)[1]
    assert (width, height) == pytest.approx((26.0, 17.0), abs=0.6), (
        "the box was resized, not moved"
    )
    assert x == pytest.approx(50.0, abs=2), "the box did not travel with the pointer"
    assert y == pytest.approx(32.0, abs=2)


def test_the_box_cannot_be_dragged_off_the_frame(page, open_editor):
    open_editor()
    _select(page, STATIONARY_BLUR)
    _seek(page, _blur(STATIONARY_BLUR).start_time)

    # Aim far past the bottom-right corner; the pointer is clamped to the frame.
    _move_rig_to(page, 140, 140)

    _, x, y, width, height = _saved_points(page, STATIONARY_BLUR, 1)[0]
    assert 0 <= x and x + width <= 100.01, f"escaped horizontally: {x} + {width}"
    assert 0 <= y and y + height <= 100.01, f"escaped vertically: {y} + {height}"


# --- the auto-keyframe rule --------------------------------------------------


def test_dragging_at_a_new_time_adds_a_point_and_leaves_the_others_alone(
    page, open_editor
):
    """The bug this phase exists to kill.

    The old handler sent the selected position's id together with the current playhead, so moving
    the box at a time between two points silently retimed one of them - losing coverage at the
    time it used to apply to, which for a blur means exposing what it was covering.

    This is also the only way a point is ever added, now that the create gestures are gone: move
    the box somewhere the blur has no point yet and one appears there.
    """
    open_editor()
    _select(page, MOVING_BLUR)
    before = _stored_points(MOVING_BLUR)
    assert [point[0] for point in before] == [3.0, 7.0, 11.0]

    _seek(page, 5.0)
    _move_rig_to(page, 60, 60)

    after = _saved_points(page, MOVING_BLUR, 4)
    assert [point[0] for point in after] == [3.0, 5.0, 7.0, 11.0]
    # Every pre-existing point is byte-for-byte untouched.
    assert [point for point in after if point[0] != 5.0] == before

    added = next(point for point in after if point[0] == 5.0)
    assert added[1] == pytest.approx(60, abs=2)
    assert added[2] == pytest.approx(60, abs=2)
    # The interpolated size at t=5.0, carried through unchanged: moving the box is not resizing it.
    assert added[3:] == pytest.approx((24.0, 15.5), abs=0.6)


def test_dragging_at_an_existing_point_updates_it_without_adding_one(page, open_editor):
    open_editor()
    _select(page, MOVING_BLUR)
    _seek(page, 7.0)

    _move_rig_to(page, 30, 65)

    after = _saved_points(page, MOVING_BLUR, 3)
    assert [point[0] for point in after] == [3.0, 7.0, 11.0], (
        "a duplicate point was added"
    )
    _, x, y, width, height = next(point for point in after if point[0] == 7.0)
    assert (x, y) == pytest.approx((30, 65), abs=2)
    assert (width, height) == pytest.approx((26.0, 17.0), abs=0.6)


def test_the_rig_shows_the_interpolated_box_between_points(page, open_editor):
    """The rig has to sit on the blur it is editing, or the user drags the wrong thing.

    At t=5.0, halfway between the seeded points at t=3.0 and t=7.0, that means the tween.
    """
    open_editor()
    _select(page, MOVING_BLUR)
    _seek(page, 5.0)

    rig = _rig_percent(page)
    assert rig["x"] == pytest.approx(26.25, abs=0.6)
    assert rig["y"] == pytest.approx(26.0, abs=0.6)
    assert rig["width"] == pytest.approx(24.0, abs=0.6)
    assert rig["height"] == pytest.approx(15.5, abs=0.6)


def test_the_rig_keeps_up_with_the_blur_during_playback(page, open_editor):
    """The rig has to move at the rate the player moves the blur, not the rate of `timeupdate`.

    The player interpolates its blur every animation frame (applyAnnotations reschedules itself
    while playing), so a rig driven only by `timeupdate` - which browsers fire a few times a second
    - visibly trailed the region it is drawn around, on exactly the moving blurs where being able
    to see what is covered matters most.

    Counting distinct positions is the same measure test_editor_scrubbing.py uses for the two
    scrubbers, and for the same reason: it distinguishes "updated per frame" from "updated on
    timeupdate" without asserting anything about a particular frame's geometry.
    """
    open_editor()
    _select(page, MOVING_BLUR)

    # Plays across the seeded path's 3.0-7.0 leg, where the box travels 12.5% -> 40.0%, so every
    # frame has a visibly different position to land on.
    result = page.evaluate(
        """async () => {
            const video = document.querySelector('.annotation-player-container video');
            const rig = document.getElementById('blur-edit-rig');
            video.muted = true;
            video.currentTime = 3.2;
            await video.play();
            const startTime = video.currentTime;
            const lefts = new Set();
            await new Promise((resolve) => {
                const startTs = performance.now();
                const tick = () => {
                    lefts.add(rig.style.left);
                    if (performance.now() - startTs > 800) resolve();
                    else requestAnimationFrame(tick);
                };
                requestAnimationFrame(tick);
            });
            const advanced = video.currentTime - startTime;
            video.pause();
            return { distinct: lefts.size, advanced, hidden: rig.hidden };
        }"""
    )

    assert result["advanced"] > 0.2, "video did not actually play"
    assert not result["hidden"], (
        "the rig was hidden during playback inside the blur's window"
    )
    # timeupdate alone would give roughly one position per 250ms, so about 4 across this window.
    assert result["distinct"] > 10, (
        f"the rig moved to only {result['distinct']} distinct positions while the player "
        "repainted the blur every frame, so the outline lags behind what it covers"
    )


def test_the_rig_hides_outside_the_blurs_time_range_and_comes_back(page, open_editor):
    """The player destroys and rebuilds its blur div around the window; the rig must not care.

    An editable box at a time when the blur does not exist would also invite an edit that cannot
    be stored, so it is hidden rather than left showing stale geometry.
    """
    open_editor()
    _select(page, MOVING_BLUR)
    _seek(page, 5.0)
    expect(page.locator("#blur-edit-rig")).to_be_visible()

    _seek(page, 20.0)
    expect(page.locator("#blur-edit-rig")).to_be_hidden()

    _seek(page, 9.0)
    expect(page.locator("#blur-edit-rig")).to_be_visible()
    rig = _rig_percent(page)
    assert rig["x"] == pytest.approx(53.25, abs=0.6)


def test_escape_abandons_a_drag_without_saving(page, open_editor):
    open_editor()
    _select(page, MOVING_BLUR)
    _seek(page, 7.0)
    before = _stored_points(MOVING_BLUR)

    # Grab the box itself and abandon the drag part-way.
    rig = _rig_percent(page)
    grab = _frame_point(
        page, (rig["x"] + rig["width"] / 2, rig["y"] + rig["height"] / 2)
    )
    page.mouse.move(*grab)
    page.mouse.down()
    page.mouse.move(*_frame_point(page, (85, 85)), steps=6)
    page.keyboard.press("Escape")
    page.mouse.up()

    page.wait_for_timeout(400)
    assert _stored_points(MOVING_BLUR) == before
    # And the box is back on the stored geometry rather than left where the pointer was.
    assert _rig_percent(page)["x"] == pytest.approx(40.0, abs=1)


# --- the points panel and the timeline dots ---------------------------------


def test_deleting_a_point_updates_the_frame_without_a_reload(page, open_editor):
    open_editor()
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


def test_the_first_point_cannot_be_deleted(page, open_editor):
    """It supplies the geometry the blur starts with, and a blur with none cannot render."""
    open_editor()
    _select(page, MOVING_BLUR)

    first_row = page.locator("#blur-positions-wrapper .position-entry").first
    expect(first_row.locator(".blur-position-delete-button")).to_have_count(0)
    expect(first_row.locator(".position-time-input")).to_have_attribute("readonly", "")


def test_the_first_points_time_looks_as_uneditable_as_it_is(page, open_editor):
    """It always equals the blur's start time, so it is derived rather than entered.

    `readonly` alone is invisible: in a row of otherwise identical fields it invites a click and then
    silently swallows the keystrokes. It has to be greyed - and still legible, since the value is
    something the user reads rather than dead chrome, which is why the contrast is asserted too. The
    field keeps its opaque fill for that reason: without one, the text sits on the active row's
    semi-transparent highlight and drops to 2.7:1.
    """
    open_editor()
    _select(page, MOVING_BLUR)

    appearance = page.evaluate(
        """() => {
            const luminance = (rgb) => {
                const [r, g, b] = rgb.match(/\\d+/g).slice(0, 3).map(Number).map((value) => {
                    const channel = value / 255;
                    return channel <= 0.03928
                        ? channel / 12.92
                        : Math.pow((channel + 0.055) / 1.055, 2.4);
                });
                return 0.2126 * r + 0.7152 * g + 0.0722 * b;
            };
            const of = (row) => {
                const input = row.querySelector('.position-time-input');
                const style = getComputedStyle(input);
                const [light, dark] = [luminance(style.color),
                                       luminance(style.backgroundColor)].sort((a, b) => b - a);
                return {
                    readOnly: input.readOnly,
                    color: style.color,
                    background: style.backgroundColor,
                    cursor: style.cursor,
                    contrast: (light + 0.05) / (dark + 0.05),
                };
            };
            const rows = [...document.querySelectorAll('#positions-list .position-entry')];
            return { first: of(rows[0]), second: of(rows[1]) };
        }"""
    )
    readonly, editable = appearance["first"], appearance["second"]

    assert readonly["readOnly"] is True
    assert editable["readOnly"] is False, "row 2's time should be editable"
    # Visibly different from the editable field beside it, not just behaviourally different.
    assert readonly["color"] != editable["color"], (
        f"the readonly time is coloured like an editable one: {appearance}"
    )
    assert readonly["cursor"] != "text", (
        "a text caret cursor promises typing that will not work"
    )
    # Dimmer, but not so dim it cannot be read.
    assert readonly["contrast"] < editable["contrast"], (
        "the greying did not dim anything"
    )
    assert readonly["contrast"] >= 4.5, (
        f"greyed to {readonly['contrast']:.2f}:1, below the 4.5:1 needed to read it"
    )
    # An opaque fill, which is what makes that ratio hold whatever the row's state is.
    assert "rgba" not in readonly["background"] or "0)" not in readonly["background"], (
        f"the readonly field has no fill of its own: {readonly['background']}"
    )
    # And it still shows the real value rather than being blanked out.
    expect(
        page.locator("#positions-list .position-entry").first.locator(
            ".position-time-input"
        )
    ).to_have_value("3.00")


def test_clicking_a_panel_row_seeks_to_that_point(page, open_editor):
    open_editor()
    _select(page, MOVING_BLUR)

    page.locator("#blur-positions-wrapper .position-entry").nth(1).click()
    page.wait_for_timeout(200)
    assert page.evaluate(
        "() => document.querySelector('.annotation-player-container video').currentTime"
    ) == pytest.approx(7.0, abs=0.3)


def test_clicking_a_timeline_dot_seeks_to_that_point(page, open_editor):
    """The dots are delegated from the timeline now, because the item HTML is replaced on save."""
    annotation = open_editor() and _select(page, MOVING_BLUR)

    dots = page.locator(f"{_item_selector(annotation)} .blur-position-locator")
    # The first point's dot is deliberately omitted: it would sit on the item's resize handle.
    expect(dots).to_have_count(2)
    dots.last.click()
    page.wait_for_timeout(200)
    assert page.evaluate(
        "() => document.querySelector('.annotation-player-container video').currentTime"
    ) == pytest.approx(11.0, abs=0.3)


def test_timeline_dots_land_at_their_points_relative_offsets(page, open_editor):
    """The `calc()` in the dot's `left` was missing its closing paren, so CSS dropped it and every
    dot stacked at the left edge of the bar."""
    annotation = open_editor() and _select(page, MOVING_BLUR)

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


# --- the item bar ------------------------------------------------------------
#
# core/tests/test_blur_position_reconcile_endpoint.py covers the same two behaviours against the
# update endpoint. What these add is the path the user actually takes to reach it - the resize
# handles and the timeline drag - plus the dots landing where the retimed points now are.


def test_dragging_the_left_handle_moves_the_first_point(page, open_editor):
    """#322 item 2: the left handle sets the *start*, so the first point has to follow it.

    Before this, dragging the left handle changed the item's start while its first point stayed
    behind, leaving the blur's opening frames covered by geometry from somewhere else entirely.
    """
    annotation = open_editor() and _select(page, MOVING_BLUR)
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
    page, open_editor
):
    """A duplicate id made dragging a blur item fail roughly one time in ten.

    The editor's timeline items are id="<type>-<id>", so naming the player's blur div "blur-<id>"
    put two elements on the page under one id. getElementById returned the overlay, which carries
    no data-annotation-id, and the item drag posted to /annotations/blur/undefined/update/. It
    looked random because it depended on whether the overlay was painted at that moment.
    """
    annotation = open_editor() and _select(page, MOVING_BLUR)
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


def test_dragging_the_item_shifts_every_point_and_its_dot(page, open_editor):
    """#322 item 3, and the `calc()` fix: after a move the dots must still mark their points."""
    annotation = open_editor() and _select(page, MOVING_BLUR)
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
    page, open_editor, playhead
):
    """Creating a blur at an arbitrary playhead, then dragging it, used to make it vanish.

    Annotations are stored to the hundredth of a second by BaseAnnotation.save(), which *rounds*.
    Asking for a start of 7.3066 therefore stored 7.31 - a few milliseconds after the playhead - so
    the annotation was not active yet and the player drew nothing. The editor's bar meanwhile
    carried the unrounded value it had asked for, so the rig appeared anyway, and then hid the
    instant a save replaced that bar with the server's own HTML.

    Parametrised over the playhead because that is the whole bug: every round number passes.
    """
    open_editor()
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

    # Move the default box up and left.
    _move_rig_to(page, 15, 15)
    _wait_for_save(page)
    page.wait_for_timeout(300)

    after = state()
    assert after["visible"], "the rig vanished after its first drag"
    assert after["overlays"] == before["overlays"], (
        "the blurred box disappeared after the drag"
    )


# --- the overlay's place in the stack ----------------------------------------


def test_the_controls_do_not_cover_the_editable_frame(page, open_editor):
    """The bottom of the picture has to be reachable, because that is where subtitles are.

    .video-controls is z-index 20 against the overlay's 10, so while the controls sat on top of the
    picture they took every pointer event in that strip: the rig's bottom handles could not be
    grabbed and a blur could not be placed over a burned-in caption. In the editor the controls are
    laid out below the picture instead. Raising the overlay above them would have put the rig's own
    handles over the scrubber.
    """
    open_editor()
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

    # And a blur can be placed against the very bottom of the frame - which needs the bottom handle
    # to be grabbable there, not just the box to be draggable there.
    _drag_handle_to(page, "s", 50, 100)
    _saved_points(page, MOVING_BLUR, 4)
    _, _, y, _, height = next(
        point for point in _stored_points(MOVING_BLUR) if point[0] == 5.0
    )
    assert y + height > 95, f"could not reach the bottom of the frame: {y} + {height}"


def test_the_scrubber_is_still_clickable_while_a_blur_is_selected(page, open_editor):
    """Scrubbing between points is the entire multi-point workflow, so it cannot be blocked."""
    open_editor()
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


def test_a_blur_is_drawn_behind_a_comment(page, open_editor):
    """A comment is text the viewer has to read; a blur exists to conceal. Order matters.

    Both are position:absolute inside the overlay, so before this was stated explicitly the
    winner was whichever annotation applyAnnotations happened to append last.
    """
    open_editor()
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


def test_deleting_a_blur_removes_it_from_the_player(page, open_editor):
    """It used to stay on screen until a reload, concealing video with nothing to select.

    applyAnnotations only cleans up an element while iterating the annotation it belongs to, so a
    deleted annotation is never reached and its box is orphaned.
    """
    open_editor()
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


def test_the_editor_layout_survives_fullscreen(page, open_editor):
    """Entering fullscreen re-lays out the player, and the controls must stay clear of the picture.

    Issue #322 calls out the normal-to-fullscreen transition specifically, and this is the layout
    that transition has to hold: a flexed video with the controls in flow beneath it.
    """
    # Larger than the 1280-wide source, so "fullscreen enlarged the picture" below is a real check
    # rather than one the default viewport makes impossible.
    page.set_viewport_size({"width": 1600, "height": 1000})
    open_editor()
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
def test_moving_a_point_repeatedly_never_changes_its_size(page, open_editor, playhead):
    """A move must be exactly a move, however many times it is repeated.

    Parked a few milliseconds off a stored point, the rig correctly shows the *interpolated* rect
    for that moment - a hair along the way toward the next point. Committing that rect while the
    server snapped the write onto the point filed it as the geometry at the point's own time,
    nudging it toward its neighbour. The next drag started from the nudged value and went further,
    so width and height crept on every single release, growing or shrinking depending on which way
    the neighbouring point lay. The off-by-a-frame playheads here are the whole test: 7.0 exactly
    always passed.
    """
    open_editor()
    _select(page, MOVING_BLUR)
    _seek(page, playhead)

    def point():
        return next(p for p in _stored_points(MOVING_BLUR) if abs(p[0] - 7.0) < 0.06)

    original = point()
    assert original[3:] == (26.0, 17.0), original

    for step in range(4):
        rig = _rig_percent(page)
        _move_rig_to(page, rig["x"] + 3, rig["y"])
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


def test_selecting_a_dot_highlights_its_panel_row(page, open_editor):
    """The two views of a point have to agree, whichever one is clicked.

    Clicking a row highlighted its dot, but not the reverse: #timeline-wrapper is an *ancestor* of
    the track items, so the delegated dot handler ran only after the item's own click handler had
    already begun reloading the detail form - and the new rows came back with no highlight on them.
    """
    annotation = open_editor() and _select(page, MOVING_BLUR)
    dot = page.locator(f"{_item_selector(annotation)} .blur-position-locator").first
    expected = dot.get_attribute("data-position-id")

    dot.click()
    page.wait_for_timeout(500)

    highlighted = _highlighted(page)
    assert highlighted["dots"] == [expected]
    assert highlighted["rows"] == [expected], "the dot's row was not highlighted"


def test_selecting_a_panel_row_highlights_its_dot(page, open_editor):
    """The direction that already worked, kept honest."""
    open_editor()
    _select(page, MOVING_BLUR)

    row = page.locator("#blur-positions-wrapper .position-entry").nth(1)
    expected = row.get_attribute("data-position-id")
    row.click()
    page.wait_for_timeout(500)

    highlighted = _highlighted(page)
    assert highlighted["rows"] == [expected]
    assert highlighted["dots"] == [expected]


def test_clicking_a_dot_on_an_unselected_blur_selects_both_the_blur_and_the_point(
    page, open_editor
):
    """Selecting an item seeks to its start, so landing on the clicked point needs care."""
    from core.models import BlurAnnotation

    open_editor()
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


def test_scrubbing_onto_a_point_highlights_it(page, open_editor):
    """The highlight follows the playhead, not the last thing clicked.

    That is what makes it survive a form reload, and it also answers "which point am I editing?"
    while scrubbing - the question the panel exists to answer.
    """
    open_editor()
    _select(page, MOVING_BLUR)

    _seek(page, 5.0)  # between points
    assert _highlighted(page)["rows"] == [], (
        "a tween is not a point and should highlight nothing"
    )

    _seek(page, 11.0)  # exactly on the last point
    on_point = _highlighted(page)
    assert len(on_point["rows"]) == 1
    assert on_point["rows"] == on_point["dots"]


def test_the_highlight_clears_when_the_blur_loses_focus(page, open_editor):
    """A dot left lit on an unselected blur claims a point is being edited when none is.

    The panel rows vanish with the detail form, so the row half of the highlight cleaned itself up
    and hid this: the dots live in the track item, which outlives the selection.
    """
    annotation = open_editor() and _select(page, MOVING_BLUR)
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


def test_dots_hold_their_times_while_a_resize_handle_is_dragged(page, open_editor):
    """A dot marks an absolute time, so stretching the bar must not drag it along.

    Its `left` is a percentage *of the item*, so while the item's width was in flux the dots slid
    with it and then snapped back to their real times when placeTrackItems ran on release. The
    assertions here are taken mid-drag, before the mouse is released - which is the only moment the
    bug existed.
    """
    annotation = open_editor() and _select(page, MOVING_BLUR)
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


# --- the panel, the dots, the copy and the keyboard --------------------------
#
# Everything below is an affordance for reaching a blur point *without* a drag on the frame: by
# number, by keyboard, or by grabbing its dot. They matter because the frame gesture alone cannot
# express "the same box, a little later" or "exactly 30% wide", and because a blur is the one
# feature where being approximately right is not good enough.


def _status(page):
    return page.locator("#blur-position-status").inner_text().strip()


def _point_at(name, time):
    """The stored point at `time`, so a test can name a row by what it is rather than its index."""
    return next(
        point
        for point in _stored_points(name)
        if point[0] == pytest.approx(time, abs=0.01)
    )


def _row_for(page, index):
    return page.locator("#blur-positions-wrapper .position-entry").nth(index)


def _focus_rig(page):
    page.locator("#blur-edit-rig").focus()
    assert page.evaluate("() => document.activeElement?.id") == "blur-edit-rig"


def _current_time(page):
    return page.evaluate(
        "() => document.querySelector('.annotation-player-container video').currentTime"
    )


def test_the_panel_offers_no_way_to_add_a_point_and_announces_nothing_yet(
    page, open_editor
):
    """The two things about the panel that are contracts rather than copy.

    A point exists wherever the box has been adjusted, so an explicit "add" control would be a
    second way to do the same thing - and it is the confusion the create gestures already caused.

    The live region has to be in the DOM ahead of its first message and empty when it gets there:
    a region inserted with text already inside it is generally not announced.

    The wording of the help text itself is deliberately not asserted. It is the only place the
    bisection procedure is written down, but exact copy is an editorial choice, and a test that
    fails when someone improves a sentence teaches people to stop improving sentences.
    """
    open_editor()
    _select(page, MOVING_BLUR)

    expect(page.locator(".blur-positions-help")).to_be_visible()
    expect(page.locator("#blur-add-point-button")).to_have_count(0)

    expect(page.locator("#blur-position-status")).to_have_attribute(
        "aria-live", "polite"
    )
    assert _status(page) == ""


def test_a_save_leaves_the_help_text_and_status_line_in_place(page, open_editor):
    """Only the rows are replaced, which is what lets the status line work as a live region."""
    open_editor()
    _select(page, MOVING_BLUR)
    _seek(page, 3.0)

    page.evaluate(
        "() => document.getElementById('blur-position-status').dataset.survived = 'yes'"
    )
    _move_rig_to(page, 30, 30)
    _saved_points(page, MOVING_BLUR, 3)

    assert (
        page.locator("#blur-position-status").get_attribute("data-survived") == "yes"
    ), "the status line was destroyed and rebuilt, so its message cannot be announced"
    expect(page.locator(".blur-positions-help")).to_be_visible()


# --- the numeric fields ------------------------------------------------------


# The point at t=7.0, seeded as x=40 y=22 w=26 h=17, and its index in a _stored_points tuple.
GEOMETRY_FIELDS = [
    (".position-x-input", "12", 1, 40.0),
    (".position-y-input", "60", 2, 22.0),
    (".position-width-input", "35", 3, 26.0),
    (".position-height-input", "9", 4, 17.0),
]


@pytest.mark.parametrize("field,entered,index,was", GEOMETRY_FIELDS)
def test_a_geometry_field_changes_only_the_value_it_names(
    page, open_editor, field, entered, index, was
):
    """None of these inputs did anything before. update_annotation never read them, and the form
    flattened repeated names so every row collapsed into one value anyway.

    x and y are here because a number is sometimes the only way to say what a drag cannot: line two
    blurs up on the same edge, or nudge one by a hundredth of a percent.
    """
    open_editor()
    _select(page, MOVING_BLUR)

    _mark_panel(page)
    row = _row_for(page, 1)  # the point at t=7.0
    row.locator(field).fill(entered)
    row.locator(field).press("Enter")
    _saved_points(page, MOVING_BLUR, 3)

    point = _point_at(MOVING_BLUR, 7.0)
    assert point[index] == pytest.approx(float(entered), abs=0.01)
    assert point[index] != pytest.approx(was, abs=0.01), "nothing actually changed"
    # And nothing else moved: editing one number must not disturb the other four.
    for _, _, other, unchanged in GEOMETRY_FIELDS:
        if other != index:
            assert point[other] == pytest.approx(unchanged, abs=0.01), (
                f"field {other} changed from {unchanged} to {point[other]}"
            )
    assert point[0] == pytest.approx(7.0, abs=0.01), "the point was retimed"
    assert _status(page) == "Point updated at 7.00s"


def test_the_points_panel_is_a_real_table(page, open_editor):
    """Each row is one point and each column is one of its values, so it is tabular data.

    The header cells are what tell a screen reader which value a field holds - without them, five
    identically-shaped inputs per row are just five numbers. It was laid out with `display: grid`
    before, which is the trap this asserts against: changing `display` on a table element strips its
    implicit ARIA role in every major browser, so the markup can be semantic while the semantics are
    gone. The computed display values below are the mechanism, not a style preference.
    """
    open_editor()
    _select(page, MOVING_BLUR)

    structure = page.evaluate(
        """() => {
            const table = document.querySelector('#positions-list table');
            if (!table) return null;
            const row = table.querySelector('tbody tr.position-entry');
            const display = (el) => el && getComputedStyle(el).display;
            return {
                headers: [...table.querySelectorAll('thead th')].map((th) => ({
                    text: th.textContent.trim(), scope: th.getAttribute('scope'),
                })),
                emptyHeaderCells: table.querySelectorAll('thead th:empty').length,
                bodyRows: table.querySelectorAll('tbody tr.position-entry').length,
                rowHeaderScope: row?.querySelector('th')?.getAttribute('scope'),
                cellsPerRow: row?.children.length,
                fieldsInCells: row ? [...row.querySelectorAll('td > input')].length : 0,
                display: {
                    table: display(table),
                    row: display(row),
                    cell: display(row?.querySelector('td')),
                },
            };
        }"""
    )

    assert structure, "the blur points panel is not a table"
    assert [header["text"] for header in structure["headers"]] == [
        "Time (s)",
        "X (%)",
        "Y (%)",
        "W (%)",
        "H (%)",
    ]
    # Every column heading is scoped, and none of them is an empty header naming nothing.
    assert all(header["scope"] == "col" for header in structure["headers"])
    assert structure["emptyHeaderCells"] == 0
    assert structure["rowHeaderScope"] == "row"
    assert structure["bodyRows"] == 3
    assert structure["cellsPerRow"] == 7
    assert structure["fieldsInCells"] == 5, (
        "the five value fields should each sit in a cell"
    )
    # The part that actually preserves the roles.
    assert structure["display"] == {
        "table": "table",
        "row": "table-row",
        "cell": "table-cell",
    }, f"display was overridden, which strips the table roles: {structure['display']}"


# Two-decimal rendering is covered without a browser, and more strictly, by
# core/tests/test_blur_positions.py: PanelRenderingTests and GeometryPrecisionTests.


def test_the_time_field_retimes_its_point(page, open_editor):
    open_editor()
    _select(page, MOVING_BLUR)

    _mark_panel(page)
    row = _row_for(page, 1)
    row.locator(".position-time-input").fill("8.5")
    row.locator(".position-time-input").press("Enter")
    points = _saved_points(page, MOVING_BLUR, 3)

    assert [point[0] for point in points] == [3.0, 8.5, 11.0]
    # Retimed, not redrawn: the geometry travels with the point.
    assert _point_at(MOVING_BLUR, 8.5)[1:] == pytest.approx(
        (40.0, 22.0, 26.0, 17.0), abs=0.01
    )
    assert _status(page) == "Point moved to 8.50s"


def test_retiming_a_point_onto_another_one_reports_the_conflict(page, open_editor):
    """Two points at one time is the invariant the unique index exists to hold. The endpoint answers
    409, and the user has to be told why nothing happened.

    Asserted against the server's own constant, because the wording lives there and nowhere else:
    BlurEditor shows the response body rather than keeping a copy of the sentence. This is the test
    that fails if the client goes back to guessing the message from the status code.
    """
    from core.views_video_editor import BLUR_POSITION_TIME_TAKEN

    open_editor()
    _select(page, MOVING_BLUR)

    row = _row_for(page, 1)
    row.locator(".position-time-input").fill("11.0")
    row.locator(".position-time-input").press("Enter")
    expect(page.locator("#blur-position-status")).to_have_text(BLUR_POSITION_TIME_TAKEN)

    assert [point[0] for point in _stored_points(MOVING_BLUR)] == [3.0, 7.0, 11.0]


def test_a_non_numeric_entry_is_refused_and_the_field_goes_back(page, open_editor):
    """The row's data-* is the last thing actually saved, so it is what the field should show."""
    open_editor()
    _select(page, MOVING_BLUR)

    row = _row_for(page, 1)
    row.locator(".position-width-input").fill("not a number")
    row.locator(".position-width-input").press("Enter")
    page.wait_for_timeout(300)

    expect(row.locator(".position-width-input")).to_have_value("26.00")
    assert "not a number" in _status(page)
    assert _point_at(MOVING_BLUR, 7.0)[3] == pytest.approx(26.0, abs=0.01)


def test_enter_in_a_size_field_does_not_submit_the_whole_annotation(page, open_editor):
    """Implicit form submission would fire update_annotation, reconcile the points, and reload the
    detail form - all as a side effect of committing one number."""
    submits = []
    page.on(
        "request",
        lambda request: (
            submits.append(request.url)
            if "/annotations/blur/" in request.url and "/update/" in request.url
            else None
        ),
    )
    open_editor()
    _select(page, MOVING_BLUR)

    _mark_panel(page)
    row = _row_for(page, 1)
    row.locator(".position-width-input").fill("31")
    row.locator(".position-width-input").press("Enter")
    _saved_points(page, MOVING_BLUR, 3)

    assert submits == [], f"Enter submitted the annotation form: {submits}"


# --- dragging a dot ----------------------------------------------------------


def _drag_dot(page, annotation, index, seconds):
    """Drag one timeline dot along its bar by roughly `seconds`."""
    item = page.locator(_item_selector(annotation))
    item_box = item.bounding_box()
    span = float(item.get_attribute("data-end")) - float(
        item.get_attribute("data-start")
    )
    dot = item.locator(".blur-position-locator").nth(index)
    dot_box = dot.bounding_box()
    start_x = dot_box["x"] + dot_box["width"] / 2
    centre_y = dot_box["y"] + dot_box["height"] / 2

    page.mouse.move(start_x, centre_y)
    page.mouse.down()
    page.mouse.move(start_x + seconds / span * item_box["width"], centre_y, steps=8)
    page.mouse.up()


def test_dragging_a_dot_retimes_its_point_and_leaves_its_geometry_alone(
    page, open_editor
):
    """A dot is a point's position *in time*. Grabbing it names that row, unlike a drag on the frame
    which names a moment - so this is the one gesture that may send a position id."""
    annotation = open_editor() and _select(page, MOVING_BLUR)

    _mark_panel(page)
    _drag_dot(page, annotation, 0, 1.5)  # the point at t=7.0, later by 1.5s
    points = _saved_points(page, MOVING_BLUR, 3)

    assert points[1][0] == pytest.approx(8.5, abs=0.4), (
        f"the dot did not retime its point: {points}"
    )
    assert points[1][1:] == pytest.approx((40.0, 22.0, 26.0, 17.0), abs=0.01)
    assert [points[0][0], points[2][0]] == [3.0, 11.0], "the other points moved"
    assert "Point moved to" in _status(page)


def test_dragging_a_dot_does_not_drag_the_whole_annotation(page, open_editor):
    """The dot sits inside a `draggable="true"` track item, so a missed suppression would move the
    entire blur along the timeline instead of retiming one of its points."""
    annotation = open_editor() and _select(page, MOVING_BLUR)

    _mark_panel(page)
    _drag_dot(page, annotation, 0, 1.5)
    _saved_points(page, MOVING_BLUR, 3)

    blur = _blur(MOVING_BLUR)
    assert (blur.start_time, blur.end_time) == (3.0, 11.0)


def test_clicking_a_dot_without_moving_it_saves_nothing(page, open_editor):
    """A click has to keep meaning "seek here". Writing on every mouseup would file a point under a
    time it was already at and spend a request doing it."""
    writes = []
    page.on(
        "request",
        lambda request: (
            writes.append(request.url)
            if request.method == "POST" and "/positions/" in request.url
            else None
        ),
    )
    annotation = open_editor() and _select(page, MOVING_BLUR)

    page.locator(f"{_item_selector(annotation)} .blur-position-locator").first.click()
    page.wait_for_timeout(500)

    assert writes == [], f"a plain click on a dot wrote to the server: {writes}"
    assert _current_time(page) == pytest.approx(7.0, abs=0.3)


def test_delete_on_a_focused_dot_removes_its_point(page, open_editor):
    """The dots are the only handle a keyboard user has on an individual point."""
    annotation = open_editor() and _select(page, MOVING_BLUR)

    _mark_panel(page)
    page.locator(f"{_item_selector(annotation)} .blur-position-locator").first.focus()
    page.keyboard.press("Delete")

    assert [point[0] for point in _saved_points(page, MOVING_BLUR, 2)] == [3.0, 11.0]
    assert _status(page) == "Point deleted"


def test_enter_on_a_focused_dot_seeks_to_its_point(page, open_editor):
    annotation = open_editor() and _select(page, MOVING_BLUR)
    _seek(page, 3.0)

    page.locator(f"{_item_selector(annotation)} .blur-position-locator").last.focus()
    page.keyboard.press("Enter")
    page.wait_for_timeout(300)

    assert _current_time(page) == pytest.approx(11.0, abs=0.3)


# --- the keyboard on the rig -------------------------------------------------


def test_arrow_keys_nudge_the_box(page, open_editor):
    open_editor()
    _select(page, MOVING_BLUR)
    _seek(page, 7.0)
    _focus_rig(page)

    _mark_panel(page)
    page.keyboard.press("ArrowRight")
    page.keyboard.press("ArrowDown")
    _saved_points(page, MOVING_BLUR, 3)

    _, x, y, width, height = _point_at(MOVING_BLUR, 7.0)
    assert (x, y) == pytest.approx((40.5, 22.5), abs=0.01)
    assert (width, height) == pytest.approx((26.0, 17.0), abs=0.01), (
        "a nudge resized the box"
    )


def test_shift_arrow_takes_a_coarser_step(page, open_editor):
    open_editor()
    _select(page, MOVING_BLUR)
    _seek(page, 7.0)
    _focus_rig(page)

    _mark_panel(page)
    page.keyboard.press("Shift+ArrowLeft")
    _saved_points(page, MOVING_BLUR, 3)

    assert _point_at(MOVING_BLUR, 7.0)[1] == pytest.approx(35.0, abs=0.01)


def test_a_burst_of_nudges_is_written_once(page, open_editor):
    """One request and one stored point per keystroke would flood the endpoint and record every
    intermediate position as though the user had meant to stop there."""
    writes = []
    page.on(
        "request",
        lambda request: (
            writes.append(request.url)
            if request.method == "POST" and "/positions/" in request.url
            else None
        ),
    )
    open_editor()
    _select(page, MOVING_BLUR)
    _seek(page, 7.0)
    _focus_rig(page)

    _mark_panel(page)
    for _ in range(4):
        page.keyboard.press("ArrowRight")
    _saved_points(page, MOVING_BLUR, 3)
    page.wait_for_timeout(600)

    assert len(writes) == 1, f"{len(writes)} writes for four keystrokes: {writes}"
    # All four steps landed, so the coalescing did not drop any of them.
    assert _point_at(MOVING_BLUR, 7.0)[1] == pytest.approx(42.0, abs=0.01)


def test_the_rig_keeps_the_arrow_keys_away_from_the_player(page, open_editor):
    """The player binds bare arrows on `document` and only steps aside for inputs and textareas, so
    without stopPropagation a nudge would also seek the video and change the volume under it."""
    open_editor()
    _select(page, MOVING_BLUR)
    _seek(page, 7.0)

    # Control: with focus off the rig, the same key does seek. Without this the assertion below
    # would pass even if the key were doing nothing at all.
    page.locator("body").click(position={"x": 2, "y": 2})
    before_loose = _current_time(page)
    page.keyboard.press("ArrowRight")
    page.wait_for_timeout(300)
    assert _current_time(page) > before_loose + 0.05, (
        "the player no longer seeks on ArrowRight"
    )

    _seek(page, 7.0)
    _focus_rig(page)
    before = _current_time(page)
    page.keyboard.press("ArrowRight")
    page.wait_for_timeout(300)
    assert _current_time(page) == pytest.approx(before, abs=0.02), (
        "the nudge also seeked"
    )


def test_comma_and_period_step_the_playhead_from_the_rig(page, open_editor):
    """The arrow keys belong to the box now, so these are what is left for frame-stepping."""
    open_editor()
    _select(page, MOVING_BLUR)
    _seek(page, 7.0)
    _focus_rig(page)

    page.keyboard.press(".")
    page.wait_for_timeout(250)
    assert _current_time(page) == pytest.approx(7.1, abs=0.03)

    page.keyboard.press(",")
    page.keyboard.press(",")
    page.wait_for_timeout(250)
    assert _current_time(page) == pytest.approx(6.9, abs=0.03)


def test_enter_writes_a_nudge_without_waiting_out_the_delay(page, open_editor):
    """Enter is a commit, not a create: it exists so a keyboard user need not wait 400ms.

    On a box that has not been nudged it does nothing, which is correct - there is no such thing as
    "add a point here" any more, only "the box is here now".
    """
    open_editor()
    _select(page, MOVING_BLUR)
    _seek(page, 5.0)
    _focus_rig(page)

    # Nothing pending: Enter must not conjure a point.
    page.keyboard.press("Enter")
    page.wait_for_timeout(500)
    assert len(_stored_points(MOVING_BLUR)) == 3, "Enter added a point on its own"

    _mark_panel(page)
    page.keyboard.press("ArrowRight")
    page.keyboard.press("Enter")
    points = _saved_points(page, MOVING_BLUR, 4)

    assert [point[0] for point in points] == [3.0, 5.0, 7.0, 11.0]
    # 26.25 is the tween at t=5.0; the nudge takes it half a percent right.
    assert _point_at(MOVING_BLUR, 5.0)[1] == pytest.approx(26.75, abs=0.01)


def test_a_seek_writes_a_pending_nudge_at_the_time_it_was_made(page, open_editor):
    """A nudge is painted at once and written 400ms later, so the playhead can move in between - by
    a click on a panel row, by playback, by anything. The geometry has to stay filed under the frame
    the user was looking at, or it lands on a point they never touched.
    """
    open_editor()
    _select(page, MOVING_BLUR)
    _seek(page, 7.0)
    _focus_rig(page)

    _mark_panel(page)
    page.keyboard.press("ArrowRight")
    # Immediately, well inside the commit delay: jump to the last point.
    page.evaluate("() => window.videoPlayer.setCurrentTime(11.0)")
    _saved_points(page, MOVING_BLUR, 3)
    page.wait_for_timeout(400)

    assert _point_at(MOVING_BLUR, 7.0)[1] == pytest.approx(40.5, abs=0.01), (
        "the nudge did not land on the point it was made at"
    )
    assert _point_at(MOVING_BLUR, 11.0)[1] == pytest.approx(66.5, abs=0.01), (
        "the nudge was written against the point the playhead moved to"
    )


# --- warning before points are pruned ----------------------------------------


def test_shrinking_a_blurs_range_warns_before_dropping_points(page, open_editor):
    """reconcile_positions drops points outside the new window, which is right - the window that
    exposes the motion path shrank. But for a feature whose job is covering content that must not
    be seen, losing part of the path silently is worse than the interruption of asking."""
    asked = []
    page.on("dialog", lambda dialog: (asked.append(dialog.message), dialog.accept()))

    annotation = open_editor() and _select(page, MOVING_BLUR)
    handle = page.locator(
        f"{_item_selector(annotation)} .resize-handle-right"
    ).bounding_box()
    container = page.locator(".track-row-annotations-container").first.bounding_box()
    duration = page.evaluate(
        "() => document.querySelector('.annotation-player-container video').duration"
    )

    # Pull the right edge in by three seconds, past the point at t=11.0.
    page.mouse.move(
        handle["x"] + handle["width"] / 2, handle["y"] + handle["height"] / 2
    )
    page.mouse.down()
    page.mouse.move(
        handle["x"] + handle["width"] / 2 - 3.0 / duration * container["width"],
        handle["y"] + handle["height"] / 2,
        steps=8,
    )
    page.mouse.up()
    page.wait_for_timeout(900)

    assert len(asked) == 1, f"no warning before dropping a point: {asked}"
    assert "1 blur point falls" in asked[0]
    assert [point[0] for point in _stored_points(MOVING_BLUR)] == [3.0, 7.0]


def test_declining_the_warning_leaves_the_blur_untouched(page, open_editor):
    page.on("dialog", lambda dialog: dialog.dismiss())

    annotation = open_editor() and _select(page, MOVING_BLUR)
    item = page.locator(_item_selector(annotation))
    width_before = item.bounding_box()["width"]
    handle = item.locator(".resize-handle-right").bounding_box()
    container = page.locator(".track-row-annotations-container").first.bounding_box()
    duration = page.evaluate(
        "() => document.querySelector('.annotation-player-container video').duration"
    )

    page.mouse.move(
        handle["x"] + handle["width"] / 2, handle["y"] + handle["height"] / 2
    )
    page.mouse.down()
    page.mouse.move(
        handle["x"] + handle["width"] / 2 - 3.0 / duration * container["width"],
        handle["y"] + handle["height"] / 2,
        steps=8,
    )
    page.mouse.up()
    page.wait_for_timeout(900)

    assert [point[0] for point in _stored_points(MOVING_BLUR)] == [3.0, 7.0, 11.0]
    blur = _blur(MOVING_BLUR)
    assert (blur.start_time, blur.end_time) == (3.0, 11.0)
    # The bar goes back to matching the stored times, rather than sitting where it was dropped.
    assert item.bounding_box()["width"] == pytest.approx(width_before, abs=2)


def test_a_resize_that_keeps_every_point_asks_nothing(page, open_editor):
    """The vacuity guard for the two tests above: the warning must not fire on an ordinary resize,
    or a user would learn to click through it."""
    asked = []
    page.on("dialog", lambda dialog: (asked.append(dialog.message), dialog.accept()))

    annotation = open_editor() and _select(page, MOVING_BLUR)
    handle = page.locator(
        f"{_item_selector(annotation)} .resize-handle-right"
    ).bounding_box()
    container = page.locator(".track-row-annotations-container").first.bounding_box()
    duration = page.evaluate(
        "() => document.querySelector('.annotation-player-container video').duration"
    )

    # Out to the right: the window only grows, so nothing can fall outside it.
    page.mouse.move(
        handle["x"] + handle["width"] / 2, handle["y"] + handle["height"] / 2
    )
    page.mouse.down()
    page.mouse.move(
        handle["x"] + handle["width"] / 2 + 2.0 / duration * container["width"],
        handle["y"] + handle["height"] / 2,
        steps=8,
    )
    page.mouse.up()
    page.wait_for_timeout(900)

    assert asked == []
    assert _blur(MOVING_BLUR).end_time == pytest.approx(13.0, abs=0.4)
    assert [point[0] for point in _stored_points(MOVING_BLUR)] == [3.0, 7.0, 11.0]


# --- no Save button ----------------------------------------------------------
#
# Every field saves itself on `change`, once committed rather than once per keystroke.


def test_editing_a_field_saves_it_without_being_asked(page, open_editor):
    open_editor()
    annotation = _select(page, MOVING_BLUR)

    page.locator("#annotation_name").fill("Renamed by autosave")
    # Focus leaves the field, which is what `change` waits for.
    page.locator("#description").click()
    page.wait_for_function(
        "() => document.querySelector('.track-item[data-annotation-type=blur]"
        ".active-track-item .track-item-label')?.textContent.trim() === 'Renamed by autosave'",
        timeout=5000,
    )
    # The same annotation, renamed - and the bar it re-rendered is still the selected one, which
    # the wait above depends on: replacing it drops the class unless it is re-applied.
    assert _blur("Renamed by autosave").pk == annotation.pk


def test_typing_a_name_is_one_save_not_one_per_keystroke(page, open_editor):
    """`change` rather than `input`. A save per character would be twenty requests for one rename,
    and twenty entries in the annotation's undo history."""
    writes = []
    page.on(
        "request",
        lambda request: (
            writes.append(request.url)
            if request.method == "POST" and "/update/" in request.url
            else None
        ),
    )
    open_editor()
    _select(page, MOVING_BLUR)

    page.locator("#annotation_name").click()
    page.keyboard.type("Gulls", delay=30)
    page.locator("#description").click()
    page.wait_for_timeout(900)

    assert len(writes) == 1, f"{len(writes)} saves for one rename: {writes}"


def test_moving_through_the_form_without_editing_saves_nothing(page, open_editor):
    """The vacuity guard for the two above: focus alone must not write.

    Without it, merely selecting an annotation and tabbing past a field would add an entry to its
    undo history and refetch every annotation on the page.
    """
    writes = []
    page.on(
        "request",
        lambda request: (
            writes.append(request.url)
            if request.method == "POST" and "/update/" in request.url
            else None
        ),
    )
    open_editor()
    _select(page, MOVING_BLUR)

    page.locator("#annotation_name").click()
    page.keyboard.press("Tab")
    page.keyboard.press("Tab")
    page.locator("#description").click()
    page.locator("#annotation_name").click()
    page.wait_for_timeout(900)

    assert writes == [], f"focus alone triggered a save: {writes}"


def test_enter_commits_a_field_without_leaving_the_editor(page, open_editor):
    """Enter is the obvious way to say "done" in a text field, so it has to mean save.

    It does, and by the same route as any other commit: the browser fires `change` on Enter. That
    also means the form's `submit` listener is unreachable as the form stands, since implicit
    submission is aborted while more than one field blocks it. The no-navigation half of this
    assertion is therefore currently guaranteed by the browser rather than by our handler - it is
    asserted anyway, because losing a field would start the submissions and a submit with no
    handler takes the user out of the editor mid-session.
    """
    open_editor()
    annotation = _select(page, MOVING_BLUR)
    was = page.url

    page.locator("#annotation_name").fill("Committed with Enter")
    page.locator("#annotation_name").press("Enter")
    page.wait_for_timeout(900)

    assert page.url == was, "Enter navigated away from the editor"
    assert _blur("Committed with Enter").pk == annotation.pk


def test_retiming_a_blur_from_the_form_reconciles_its_points_in_place(
    page, open_editor
):
    """The form is not rebuilt after an auto-save - that would move the caret out of the field the
    user went on to - so the blur's point rows have to be patched from the response instead."""
    page.on("dialog", lambda dialog: dialog.accept())
    open_editor()
    _select(page, MOVING_BLUR)

    page.locator("#end_time").fill("9")
    page.locator("#annotation_name").click()
    page.wait_for_function(
        "() => document.querySelectorAll('#positions-list .position-entry').length === 2",
        timeout=5000,
    )

    assert [point[0] for point in _stored_points(MOVING_BLUR)] == [3.0, 7.0]
    # And the field now shows what was stored, not what was typed.
    expect(page.locator("#end_time")).to_have_value("9.0")


def _player_points(page, annotation):
    """The player's own copy of a blur's positions.

    Not the database and not the panel: this is the array applyAnnotations reads on every frame, so
    it is what decides whether playback actually covers the subject. A save that reaches the
    database but not here is invisible until the page is reloaded.
    """
    return page.evaluate(
        """(id) => {
            const annotation = window.videoPlayer.annotations.find((a) => a.id == id);
            return annotation ? annotation.positions.map((p) => [p.time, p.x]) : null;
        }""",
        annotation.pk,
    )


def test_a_nudge_saved_as_the_selection_moves_on_still_reaches_the_player(
    page, open_editor
):
    """Selecting another blur seeks, and seeking flushes the pending nudge - so the response to
    that save routinely arrives after the selection has already changed.

    The save is addressed correctly either way, because _commit captures the annotation id before
    its first await. What used to go wrong is that the *response* was discarded wholesale once the
    id no longer matched, so the server held the edit while the player went on painting the old
    geometry for the rest of the session.
    """
    annotation = open_editor() and _select(page, MOVING_BLUR)
    _seek(page, 7.0)
    _focus_rig(page)

    page.keyboard.press("ArrowRight")
    _select(page, STATIONARY_BLUR)
    # The nudge's own save, not the selection change, is what has to have landed.
    page.wait_for_function(
        """() => {
            const rows = document.querySelectorAll('#positions-list .position-entry');
            return rows.length === 1;
        }""",
        timeout=5000,
    )
    page.wait_for_timeout(400)

    assert _point_at(MOVING_BLUR, 7.0)[1] == pytest.approx(40.5, abs=0.01), (
        "the nudge never reached the database, so this proves nothing about the player"
    )
    assert _player_points(page, annotation) == [
        [3.0, pytest.approx(12.5, abs=0.01)],
        [7.0, pytest.approx(40.5, abs=0.01)],
        [11.0, pytest.approx(66.5, abs=0.01)],
    ], "the player is still painting the geometry from before the nudge"
