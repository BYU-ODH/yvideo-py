from playwright.sync_api import expect
import pytest

from core.models import MuteAnnotation

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.django_db(transaction=True),
]


def _select_first_mute(page, content):
    annotation = MuteAnnotation.objects.filter(
        track__annotation_set=content.annotation_set, active=True
    ).first()
    page.locator(f"#mute-{annotation.id} .track-item-content").click()
    expect(page.locator("#existing-item-form")).to_have_attribute(
        "data-annotation-id", str(annotation.id)
    )


def test_form_edit_shows_waiting_then_saved_with_check(page, open_editor):
    content = open_editor()
    status = page.locator("#annotation-save-status")
    expect(status.locator(".save-status-text")).to_have_text("")

    _select_first_mute(page, content)
    page.locator("#annotation_name").fill("Renamed mute")
    expect(status).to_contain_text("Waiting to autosave...")

    page.locator("#description").click()
    expect(status).to_contain_text("Changes saved")
    expect(status.locator(".save-status-check")).to_have_class(
        "save-status-check save-status-check-animating"
    )


def test_reverting_an_unsaved_edit_clears_waiting(page, open_editor):
    content = open_editor()
    _select_first_mute(page, content)
    name = page.locator("#annotation_name")
    original = name.input_value()

    name.fill("Temporary")
    expect(page.locator("#annotation-save-status")).to_contain_text(
        "Waiting to autosave..."
    )
    name.fill(original)
    expect(page.locator("#annotation-save-status .save-status-text")).to_have_text("")


def test_timeline_save_reports_saved(page, open_editor):
    open_editor()
    page.locator('.annotation-type-add-button[data-annotation-type="mute"]').click()
    expect(page.locator("#annotation-save-status")).to_contain_text("Changes saved")


def test_failed_save_is_reported(page, open_editor):
    content = open_editor()
    _select_first_mute(page, content)
    page.route("**/annotations/mute/*/update/", lambda route: route.fulfill(status=500))

    page.locator("#annotation_name").fill("Will fail")
    page.locator("#description").click()
    status = page.locator("#annotation-save-status")
    expect(status).to_contain_text("Save failed")

    status.get_by_role("button", name="About failed saves").click()
    expect(page.locator("#save-failed-help-modal")).to_be_visible()
    page.locator("#save-failed-help-modal").get_by_role("button", name="Close").click()
    expect(page.locator("#save-failed-help-modal")).to_be_hidden()


def test_subtitle_cue_edit_shows_status_in_the_subtitle_panel(page, open_editor):
    open_editor()
    page.locator("#annotation-panel-switch").click()
    track_value = page.locator(
        "#subtitles-track-selector option.subtitles-track-option"
    ).first.get_attribute("value")
    page.select_option("#subtitles-track-selector", track_value)
    page.wait_for_selector(".editor-subtitle-cue")

    status = page.locator("#subtitle-save-status")
    cue = page.locator(".editor-subtitle-cue-content").first
    original = cue.input_value()
    cue.fill(original + " edited")
    expect(status.locator(".save-status-text")).to_have_text("Waiting to autosave...")

    # Typing back to the saved text leaves nothing to save.
    cue.fill(original)
    expect(status.locator(".save-status-text")).to_have_text("")

    cue.fill(original + " edited")
    cue.press("Enter")
    expect(status).to_contain_text("Changes saved")
    expect(page.locator(".editor-subtitle-cue-content").first).to_have_value(
        original + " edited"
    )


def _open_first_subtitle_track(page):
    page.locator("#annotation-panel-switch").click()
    track_value = page.locator(
        "#subtitles-track-selector option.subtitles-track-option"
    ).first.get_attribute("value")
    page.select_option("#subtitles-track-selector", track_value)
    page.wait_for_selector(".editor-subtitle-cue")


def _count_cue_saves(page):
    saves = []
    page.on(
        "request",
        lambda request: (
            saves.append(request.url) if "/update-cues/" in request.url else None
        ),
    )
    return saves


def test_subtitle_cue_edit_saves_on_focusout(page, open_editor):
    open_editor()
    _open_first_subtitle_track(page)
    saves = _count_cue_saves(page)

    status = page.locator("#subtitle-save-status")
    cue = page.locator(".editor-subtitle-cue-content").first
    original = cue.input_value()
    cue.fill(original + " edited")
    expect(status.locator(".save-status-text")).to_have_text("Waiting to autosave...")

    cue.blur()
    expect(status).to_contain_text("Changes saved")
    expect(page.locator(".editor-subtitle-cue-content").first).to_have_value(
        original + " edited"
    )
    assert len(saves) == 1


def test_subtitle_cue_focusout_without_edits_does_not_save(page, open_editor):
    open_editor()
    _open_first_subtitle_track(page)
    saves = _count_cue_saves(page)

    cue = page.locator(".editor-subtitle-cue-content").first
    cue.focus()
    cue.blur()
    page.wait_for_timeout(300)
    assert saves == []


def test_subtitle_cue_reverted_after_failed_enter_saves_on_focusout(page, open_editor):
    open_editor()
    _open_first_subtitle_track(page)
    status = page.locator("#subtitle-save-status")
    cue = page.locator(".editor-subtitle-cue-content").first
    original = cue.input_value()

    # A failed save doesn't re-render the list, so the same textarea keeps focus.
    page.route("**/update-cues/", lambda route: route.fulfill(status=500))
    cue.fill(original + " edited")
    cue.press("Enter")
    expect(status).to_contain_text("Save failed")
    page.unroute("**/update-cues/")

    # Typing back to the value the field had when focused fires no "change" event,
    # but it still differs from what was last sent, so leaving the field must save it.
    saves = _count_cue_saves(page)
    cue.fill(original)
    cue.blur()
    expect(status).to_contain_text("Changes saved")
    assert len(saves) == 1


def test_subtitle_cue_failed_enter_save_is_retried_on_focusout(page, open_editor):
    open_editor()
    _open_first_subtitle_track(page)
    status = page.locator("#subtitle-save-status")
    cue = page.locator(".editor-subtitle-cue-content").first
    original = cue.input_value()

    page.route("**/update-cues/", lambda route: route.fulfill(status=500))
    cue.fill(original + " edited")
    cue.press("Enter")
    expect(status).to_contain_text("Save failed")
    page.unroute("**/update-cues/")

    # Nothing was typed since the failure, but the edit was never stored, so leaving the field
    # must send it again.
    saves = _count_cue_saves(page)
    cue.blur()
    expect(status).to_contain_text("Changes saved")
    expect(page.locator(".editor-subtitle-cue-content").first).to_have_value(
        original + " edited"
    )
    assert len(saves) == 1


def test_subtitle_cue_enter_then_focusout_saves_once(page, open_editor):
    open_editor()
    _open_first_subtitle_track(page)
    saves = _count_cue_saves(page)
    status = page.locator("#subtitle-save-status")
    cue = page.locator(".editor-subtitle-cue-content").first
    cue.fill(cue.input_value() + " edited")
    cue.press("Enter")
    expect(status).to_contain_text("Changes saved")

    page.locator(".editor-subtitle-cue-content").first.blur()
    expect(status).to_contain_text("Changes saved")
    assert len(saves) == 1


def _focused_cue_field(page):
    """The focused element's class, and the position of its cue in the list."""
    return page.evaluate(
        """() => {
            const el = document.activeElement;
            const cue = el?.closest(".editor-subtitle-cue");
            const cues = Array.from(document.querySelectorAll(".editor-subtitle-cue"));
            return cue ? [el.className, cues.indexOf(cue)] : [el?.id ?? null, -1];
        }"""
    )


def test_subtitle_cue_enter_save_keeps_focus_and_caret(page, open_editor):
    open_editor()
    _open_first_subtitle_track(page)
    status = page.locator("#subtitle-save-status")
    cue = page.locator(".editor-subtitle-cue-content").first
    edited = cue.input_value() + " edited"
    cue.fill(edited)
    cue.evaluate("el => el.setSelectionRange(2, 4)")
    cue.press("Enter")
    expect(status).to_contain_text("Changes saved")

    assert _focused_cue_field(page) == ["editor-subtitle-cue-content", 0]
    rebuilt = page.locator(".editor-subtitle-cue-content").first
    assert rebuilt.evaluate("el => [el.selectionStart, el.selectionEnd]") == [2, 4]
    # Typing goes on where the user left off.
    page.keyboard.type("X")
    expect(rebuilt).to_have_value(edited[:2] + "X" + edited[4:])


def test_subtitle_cue_save_keeps_focus_on_the_field_clicked_next(page, open_editor):
    open_editor()
    _open_first_subtitle_track(page)
    status = page.locator("#subtitle-save-status")
    first = page.locator(".editor-subtitle-cue-content").nth(0)
    first.fill(first.input_value() + " edited")

    # Clicking the next cue blurs the first, whose save rebuilds the list under the click.
    page.locator(".editor-subtitle-cue-content").nth(1).click()
    expect(status).to_contain_text("Changes saved")
    assert _focused_cue_field(page) == ["editor-subtitle-cue-content", 1]


def _timestamp(seconds):
    milliseconds = round(seconds * 1000)
    hours, milliseconds = divmod(milliseconds, 3_600_000)
    minutes, milliseconds = divmod(milliseconds, 60_000)
    secs, milliseconds = divmod(milliseconds, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}.{milliseconds:03d}"


def test_subtitle_cue_focus_follows_a_cue_the_save_moves(page, open_editor):
    open_editor()
    _open_first_subtitle_track(page)
    status = page.locator("#subtitle-save-status")
    cues = page.locator(".editor-subtitle-cue")
    assert cues.count() >= 2
    second_start = float(cues.nth(1).get_attribute("data-start-time"))
    second_end = float(
        cues.nth(1).locator(".editor-subtitle-cue-end").get_attribute("data-end-time")
    )
    first_text = cues.nth(0).locator(".editor-subtitle-cue-content").input_value()

    # Move the first cue to just after the second: end first, so the cue stays valid.
    cues.nth(0).locator(".editor-subtitle-cue-end").fill(_timestamp(second_end + 1))
    cues.nth(0).locator(".editor-subtitle-cue-start").click()
    expect(status).to_contain_text("Changes saved")
    assert _focused_cue_field(page) == ["editor-subtitle-cue-start", 0]

    page.keyboard.press("ControlOrMeta+a")
    page.keyboard.type(_timestamp(second_start + 0.001))
    page.keyboard.press("Tab")
    expect(cues.nth(1).locator(".editor-subtitle-cue-content")).to_have_value(
        first_text
    )
    expect(status).to_contain_text("Changes saved")
    # The Tab went to the moved cue's end time, so focus follows that cue to its new place.
    assert _focused_cue_field(page) == ["editor-subtitle-cue-end", 1]


def test_subtitle_cue_delete_moves_focus_to_the_next_cue(page, open_editor):
    open_editor()
    _open_first_subtitle_track(page)
    status = page.locator("#subtitle-save-status")
    cues = page.locator(".editor-subtitle-cue")
    count = cues.count()
    second_text = cues.nth(1).locator(".editor-subtitle-cue-content").input_value()

    cues.nth(0).locator(".editor-subtitle-cue-delete").focus()
    page.keyboard.press("Enter")
    expect(cues).to_have_count(count - 1)
    expect(status).to_contain_text("Changes saved")
    expect(cues.nth(0).locator(".editor-subtitle-cue-content")).to_have_value(
        second_text
    )
    assert _focused_cue_field(page) == [
        "item-form-icon-button editor-subtitle-cue-delete",
        0,
    ]
