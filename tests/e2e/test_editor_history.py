import re

from playwright.sync_api import expect
import pytest

from core.models import CommentAnnotation
from core.models import MuteAnnotation

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.django_db(transaction=True),
]

# Seeded on the birds track at 8.0-15.0s. Clicking its item seeks the video into that range, which
# is what makes the player draw the comment's box.
SEEDED_COMMENT = "Bird Notes 1"


def _select(page, annotation):
    """Open an annotation in the detail form the way a user does - from the timeline."""
    annotation_type = annotation.annotation_type
    page.locator(f"#{annotation_type}-{annotation.id} .track-item-content").click()
    expect(page.locator("#existing-item-form")).to_have_attribute(
        "data-annotation-id", str(annotation.id)
    )
    return annotation


def _active_form_id(page):
    return page.locator("#existing-item-form").get_attribute("data-annotation-id")


def _seek(page, seconds):
    page.evaluate("(t) => window.videoPlayer.setCurrentTime(t)", seconds)
    # `seeking` and not `currentTime` alone: the property reports the requested time as soon as it
    # is assigned, while the seek - and so the `seeked` handler everything here is waiting on - is
    # still in flight.
    page.wait_for_function(
        """(t) => {
            const video = document.querySelector('.annotation-player-container video');
            return !video.seeking && Math.abs(video.currentTime - t) < 0.2;
        }""",
        arg=seconds,
        timeout=5000,
    )
    # One frame for the seeked handler to settle the rig's visibility.
    page.wait_for_timeout(150)


def _commit_name(page, name, previous_id):
    """Type a name and commit it, then wait for the save to advance the form to a new version.

    There is no save button: fields save themselves on `change`, which fires when focus leaves.
    """
    page.locator("#annotation_name").fill(name)
    page.locator("#description").click()
    page.wait_for_function(
        "(previous) => document.querySelector('#existing-item-form')?.dataset.annotationId"
        " !== previous",
        arg=str(previous_id),
    )
    return _active_form_id(page)


def test_edit_undo_redo_and_branching_stay_in_sync(page, open_editor):
    content = open_editor()
    annotation = _select(
        page,
        MuteAnnotation.objects.filter(
            track__annotation_set=content.annotation_set, active=True
        ).first(),
    )
    original_id = str(annotation.id)
    original_name = annotation.name

    toolbar = page.locator(".item-form-header .undo-redo-toolbar")
    expect(toolbar).to_be_visible()
    expect(toolbar.locator(".undo-btn img")).to_have_attribute(
        "src", re.compile(r"undo(?:\.[a-z0-9]+)?\.svg$")
    )
    expect(toolbar.locator(".redo-btn img")).to_have_attribute(
        "src", re.compile(r"redo(?:\.[a-z0-9]+)?\.svg$")
    )
    # Document order rather than coordinates: the requirement is that the controls come before the
    # fields they act on, and a bounding box also fails for reasons that have nothing to do with
    # that - a wrapped header, a scrolled panel, a font that renders a pixel taller.
    assert page.evaluate(
        """() => {
            const toolbar = document.querySelector('.item-form-header .undo-redo-toolbar');
            const form = document.getElementById('annotation-update-form');
            return Boolean(toolbar.compareDocumentPosition(form)
                & Node.DOCUMENT_POSITION_FOLLOWING);
        }"""
    ), "the history controls should precede the fields they act on"
    expect(toolbar.locator(".undo-btn")).to_be_disabled()
    expect(toolbar.locator(".redo-btn")).to_be_disabled()

    edited_id = _commit_name(page, "History edit", original_id)

    expect(page.locator(f"#mute-{original_id}")).to_have_count(0)
    expect(page.locator(f"#mute-{edited_id}")).to_be_visible()
    expect(page.locator(f"#mute-panel-item-{edited_id}")).to_contain_text(
        "History edit"
    )
    expect(page.locator(".undo-btn")).to_be_enabled()
    expect(page.locator(".redo-btn")).to_be_disabled()

    page.locator(".undo-btn").click()
    expect(page.locator("#existing-item-form")).to_have_attribute(
        "data-annotation-id", original_id
    )
    expect(page.locator("#annotation_name")).to_have_value(original_name)
    expect(page.locator(f"#mute-{original_id}")).to_be_visible()
    expect(page.locator(".redo-btn")).to_be_enabled()

    page.locator(".redo-btn").click()
    expect(page.locator("#existing-item-form")).to_have_attribute(
        "data-annotation-id", edited_id
    )
    expect(page.locator("#annotation_name")).to_have_value("History edit")

    # Editing after an undo creates a new linear branch and clears redo.
    page.locator(".undo-btn").click()
    expect(page.locator("#existing-item-form")).to_have_attribute(
        "data-annotation-id", original_id
    )
    replacement_id = _commit_name(page, "Replacement edit", original_id)

    assert replacement_id != edited_id
    expect(page.locator("#annotation_name")).to_have_value("Replacement edit")
    expect(page.locator(".redo-btn")).to_be_disabled()


def test_keyboard_shortcut_undoes_the_open_annotation(page, open_editor):
    content = open_editor()
    annotation = _select(
        page,
        MuteAnnotation.objects.filter(
            track__annotation_set=content.annotation_set, active=True
        ).first(),
    )
    original_id = str(annotation.id)
    _commit_name(page, "Undone by keyboard", original_id)

    # Off the text fields on purpose: inside one, Ctrl+Z has to stay the browser's own undo.
    page.locator(".item-form-header h3").click()
    page.keyboard.press("Control+z")

    expect(page.locator("#existing-item-form")).to_have_attribute(
        "data-annotation-id", original_id
    )
    expect(page.locator("#annotation_name")).to_have_value(annotation.name)


def test_autosave_does_not_move_the_playhead(page, open_editor):
    """A background save must not double as "go to this annotation".

    Saving re-renders the timeline item and panel item under a new id, so their active styling has
    to be re-applied - but re-applying it by way of the full selection path also seeks the video to
    the annotation's start, throwing away the frame the user is working at. BlurEditor avoids this
    by re-applying the class by hand; the version-response path has to avoid it too.
    """
    content = open_editor()
    annotation = _select(
        page,
        CommentAnnotation.objects.get(
            name=SEEDED_COMMENT,
            track__annotation_set=content.annotation_set,
            active=True,
        ),
    )

    # Part-way into the annotation rather than at its start, so a seek back to the start is
    # measurable at all.
    target = float(annotation.start_time) + 3
    page.evaluate("(t) => { document.querySelector('video').currentTime = t; }", target)
    page.wait_for_function(
        "(t) => Math.abs(document.querySelector('video').currentTime - t) < 0.3",
        arg=target,
    )

    _commit_name(page, "Renamed without seeking", str(annotation.id))

    assert page.evaluate(
        "() => document.querySelector('video').currentTime"
    ) == pytest.approx(target, abs=0.5), (
        "the autosave seeked the video away from the frame being edited"
    )


def test_editing_a_comment_field_creates_exactly_one_version(page, open_editor):
    """The comment inputs save on `input` (debounced) and the form saves on `change`.

    Both commit the same edit, so unless they share one save path a single change to one of these
    fields writes two identical versions - and the user has to press undo twice to take back what
    they did once.
    """
    content = open_editor()
    annotation = _select(
        page,
        CommentAnnotation.objects.get(
            name=SEEDED_COMMENT,
            track__annotation_set=content.annotation_set,
            active=True,
        ),
    )
    original_id = str(annotation.id)

    def version_count():
        return CommentAnnotation.objects.filter(
            track__annotation_set=content.annotation_set, name=annotation.name
        ).count()

    # The comment inputs only save while the player is drawing the box, so wait for it.
    expect(page.locator(f"#comment-text-box-{original_id}")).to_be_visible()
    before = version_count()

    # Typed, then left alone long enough for the 250 ms debounce to commit, and only then blurred.
    # The pause is the point: it separates the two save paths, so the `change` that follows is
    # judged against whatever baseline the debounced save left behind. Blurring immediately
    # instead overlaps the two requests, and the second is then rejected for addressing a
    # superseded id - a different bug, and one that hides this one.
    page.locator("#font-size").fill("2.5")
    page.wait_for_timeout(800)
    page.locator("#description").click()
    page.wait_for_timeout(1200)

    added = version_count() - before
    assert added == 1, f"one edit should add one version, added {added}"

    # And one undo is enough to get back to where they started.
    page.locator(".undo-btn").click()
    expect(page.locator("#existing-item-form")).to_have_attribute(
        "data-annotation-id", original_id
    )


def test_comment_box_is_still_editable_after_a_save_makes_a_new_version(
    page, open_editor
):
    content = open_editor()
    annotation = _select(
        page,
        CommentAnnotation.objects.get(
            name=SEEDED_COMMENT,
            track__annotation_set=content.annotation_set,
            active=True,
        ),
    )
    original_id = str(annotation.id)

    expect(page.locator(f"#comment-text-box-{original_id}")).to_be_visible()
    rig = page.locator("#comment-edit-rig")
    expect(rig).to_be_visible()
    expect(rig.locator(".comment-rig-handle")).to_have_count(8)

    new_id = _commit_name(page, "Renamed comment", original_id)

    # The player keys its box by annotation id, so the save leaves the old one to be removed and a
    # bare one built under the new id. The rig is the editor's own element and is keyed by nothing,
    # so it survives that untouched - which is the whole point of it. Before, the controls lived on
    # the player's box and the box the user was looking at silently stopped responding.
    expect(page.locator(f"#comment-text-box-{new_id}")).to_be_visible()
    expect(rig).to_be_visible()
    expect(rig.locator(".comment-rig-handle")).to_have_count(8)


def test_comment_rig_is_hidden_while_the_playhead_is_outside_the_comment(
    page, open_editor
):
    """The rig is an editable outline, so it may only be on screen where the box it edits is.

    The player draws its comment box only within [start, end); a rig that outlived it would offer a
    grip on nothing, and a drag there would write geometry the user could not see.
    """
    content = open_editor()
    _select(
        page,
        CommentAnnotation.objects.get(
            name=SEEDED_COMMENT,
            track__annotation_set=content.annotation_set,
            active=True,
        ),
    )
    rig = page.locator("#comment-edit-rig")
    expect(rig).to_be_visible()

    # Seeded at 8.0-15.0, so 2.0 is before it and 20.0 after.
    _seek(page, 2.0)
    expect(rig).to_be_hidden()
    expect(page.locator(".comment-text-box")).to_have_count(0)

    _seek(page, 20.0)
    expect(rig).to_be_hidden()

    # And it comes back, rather than needing the annotation reselected.
    _seek(page, 10.0)
    expect(rig).to_be_visible()
    expect(rig.locator(".comment-rig-handle")).to_have_count(8)


def test_selecting_another_annotation_type_takes_the_comment_rig_away(
    page, open_editor
):
    """Nothing used to remove the controls when focus moved to a different type of annotation.

    They were children of the player's comment box, so they stayed on screen - still dragging and
    saving the comment they were built for, from a form that no longer described it.
    """
    content = open_editor()
    _select(
        page,
        CommentAnnotation.objects.get(
            name=SEEDED_COMMENT,
            track__annotation_set=content.annotation_set,
            active=True,
        ),
    )
    expect(page.locator("#comment-edit-rig")).to_be_visible()

    _select(
        page,
        MuteAnnotation.objects.filter(
            track__annotation_set=content.annotation_set, active=True
        ).first(),
    )
    expect(page.locator("#comment-edit-rig")).to_have_count(0)


def _drag_grip(page, handle_name, by=None, to=None, measure=False):
    """Drag one of the open comment box's eight grips and wait out the save it commits.

    `by` is a pixel offset, `to` a fraction of the video frame. `measure` reports the box while the
    pointer is still down - the editor's own arithmetic, before the server has had its say.

    Pointer-up writes a new version, so the box afterwards is a fresh element under a new id.
    Waiting for the form to advance *and* the grips to come back is what stops the next step from
    addressing a box that is about to be replaced.
    """
    previous_id = _active_form_id(page)
    grip = page.locator(
        f'#comment-edit-rig [data-handle="{handle_name}"]'
    ).bounding_box()
    start_x = grip["x"] + grip["width"] / 2
    start_y = grip["y"] + grip["height"] / 2
    if to is not None:
        frame = page.locator("#annotation-box").bounding_box()
        end_x = frame["x"] + frame["width"] * to[0]
        end_y = frame["y"] + frame["height"] * to[1]
    else:
        end_x, end_y = start_x + by[0], start_y + by[1]

    page.mouse.move(start_x, start_y)
    page.mouse.down()
    page.mouse.move(end_x, end_y, steps=5)
    dragged = _box_rect(page) if measure else None
    page.mouse.up()

    page.wait_for_function(
        "(previous) => document.querySelector('#existing-item-form')?.dataset.annotationId"
        " !== previous",
        arg=str(previous_id),
    )
    expect(page.locator("#comment-edit-rig .comment-rig-handle")).to_have_count(8)
    return dragged


def _resize_box(page, previous_id):
    """Drag a corner, which saves a new version on pointer-up."""
    _drag_grip(page, "se", by=(40, 30))
    return _active_form_id(page)


def _box_rect(page):
    """The rig's rect - the editor's own arithmetic, and what a release writes to the form."""
    return page.evaluate(
        """() => {
            const rig = document.querySelector('#comment-edit-rig');
            return {
                left: parseFloat(rig.style.left),
                top: parseFloat(rig.style.top),
                width: parseFloat(rig.style.width),
                height: parseFloat(rig.style.height),
            };
        }"""
    )


def test_comment_box_edge_handles_resize_one_axis(page, open_editor):
    """The eight grips are shared with the blur rig, so an edge grip moves one edge and no more."""
    content = open_editor()
    _select(
        page,
        CommentAnnotation.objects.get(
            name=SEEDED_COMMENT,
            track__annotation_set=content.annotation_set,
            active=True,
        ),
    )
    # A corner drag first, to give the box a rectangle to work from: the seeded comment stores its
    # bottom-right corner above and left of its top-left one, so it starts with no size at all.
    # Dragged to a point rather than by an offset, so the box ends up small enough that the edge
    # drags below cannot run into the frame and be clamped, which would move an edge they must not.
    _drag_grip(page, "se", to=(0.4, 0.5))

    before = _box_rect(page)
    widened = _drag_grip(page, "e", by=(30, 0), measure=True)

    assert widened["width"] > before["width"] + 2, (before, widened)
    assert widened["left"] == pytest.approx(before["left"], abs=0.5)
    assert widened["top"] == pytest.approx(before["top"], abs=0.5)
    assert widened["height"] == pytest.approx(before["height"], abs=0.5)

    before = _box_rect(page)
    raised = _drag_grip(page, "n", by=(0, -30), measure=True)

    assert raised["top"] < before["top"] - 1, (before, raised)
    assert raised["height"] > before["height"] + 1
    assert raised["left"] == pytest.approx(before["left"], abs=0.5)
    assert raised["width"] == pytest.approx(before["width"], abs=0.5)


def test_superseded_comment_versions_leave_no_box_behind(page, open_editor):
    """Each save is a new annotation id, and the player draws one box per id.

    Nothing on screen distinguishes a superseded version's box from the live one, so leaving them
    behind stacks copies of the comment over the video. Ids are handed out per annotation table, so
    the cleanup has to match on type as well - a comment sharing a number with some blur is still a
    dead comment.
    """
    content = open_editor()
    annotation = _select(
        page,
        CommentAnnotation.objects.get(
            name=SEEDED_COMMENT,
            track__annotation_set=content.annotation_set,
            active=True,
        ),
    )

    current_id = _commit_name(page, "Renamed once", str(annotation.id))
    for _ in range(3):
        current_id = _resize_box(page, current_id)

    expect(page.locator(".comment-text-box")).to_have_count(1)
    expect(page.locator(f"#comment-text-box-{current_id}")).to_be_visible()
    # Every overlay still on screen belongs to an annotation the player considers live.
    assert page.evaluate(
        """() => {
            const live = new Set((window.videoPlayer.annotations || [])
                .map((a) => `${a.type}:${a.id}`));
            return [...document.querySelectorAll('#annotation-box [data-annotation-id]')]
                .every((el) => live.has(
                    `${el.dataset.annotationType}:${el.dataset.annotationId}`));
        }"""
    ), "an overlay outlived the annotation version it was drawn for"


def test_deleting_an_annotation_asks_first(page, open_editor):
    content = open_editor()
    annotation = _select(
        page,
        MuteAnnotation.objects.filter(
            track__annotation_set=content.annotation_set, active=True
        ).first(),
    )

    page.once("dialog", lambda dialog: dialog.dismiss())
    page.locator("#annotation-form-delete-button").click()

    # Dismissed, so nothing was deleted: the item is still on the timeline and still open.
    expect(page.locator(f"#mute-{annotation.id}")).to_be_visible()
    expect(page.locator("#existing-item-form")).to_have_attribute(
        "data-annotation-id", str(annotation.id)
    )

    page.once("dialog", lambda dialog: dialog.accept())
    page.locator("#annotation-form-delete-button").click()

    expect(page.locator(f"#mute-{annotation.id}")).to_have_count(0)
    expect(page.locator(f"#mute-panel-item-{annotation.id}")).to_have_count(0)
