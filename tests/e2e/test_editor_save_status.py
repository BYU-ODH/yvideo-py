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

    status.get_by_role("button", name="Why did saving fail?").click()
    expect(page.locator("#save-failed-help-modal")).to_be_visible()
    page.locator("#save-failed-help-modal").get_by_role("button", name="Close").click()
    expect(page.locator("#save-failed-help-modal")).to_be_hidden()
