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
