from playwright.sync_api import expect
import pytest

from core.models import Track

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.django_db(transaction=True),
]


def _open_rename_field(page, track_row):
    track_row.locator(".open-multi-select-button").click()
    track_row.locator(".track-menu-rename").click()
    rename_input = track_row.locator(".track-rename-input")
    expect(rename_input).to_be_focused()
    expect(track_row.locator(".track-name")).to_be_hidden()
    return rename_input


def test_enter_saves_the_name_and_closes_the_field(page, open_editor):
    open_editor()
    track_row = page.locator(".track-row").first
    track_id = track_row.get_attribute("data-track-id")

    rename_input = _open_rename_field(page, track_row)
    rename_input.fill("  Renamed track  ")
    rename_input.press("Enter")

    expect(rename_input).to_be_hidden()
    expect(track_row.locator(".track-name")).to_have_text("Renamed track")
    expect(track_row).to_have_attribute("data-track-name", "Renamed track")
    expect(track_row.locator(".track-menu-rename")).to_have_attribute(
        "aria-label", "Rename track Renamed track"
    )
    assert Track.objects.get(pk=track_id).name == "Renamed track"


@pytest.mark.parametrize("leave", ["Escape", "blur", "empty"])
def test_leaving_without_a_new_name_restores_the_old_one(page, open_editor, leave):
    open_editor()
    track_row = page.locator(".track-row").first
    track_id = track_row.get_attribute("data-track-id")
    original_name = track_row.locator(".track-name").inner_text()

    rename_input = _open_rename_field(page, track_row)
    if leave == "empty":
        rename_input.fill("   ")
        rename_input.press("Enter")
    else:
        rename_input.fill("Abandoned name")
        if leave == "Escape":
            rename_input.press("Escape")
        else:
            rename_input.blur()

    expect(rename_input).to_be_hidden()
    expect(track_row.locator(".track-name")).to_have_text(original_name)
    expect(rename_input).to_have_value(original_name)
    assert Track.objects.get(pk=track_id).name == original_name


def test_annotation_set_title_uses_the_same_rename_flow(page, open_editor):
    open_editor()
    title = page.locator("#annotation-set-title")
    title_input = page.locator("#annotation-set-title-input")
    edit_button = page.locator("#annotation-set-edit-button")
    original_name = title.inner_text()

    edit_button.click()
    expect(title_input).to_be_focused()
    expect(title).to_be_hidden()
    expect(edit_button).to_be_hidden()
    title_input.fill("Abandoned name")
    title_input.blur()
    expect(title_input).to_be_hidden()
    expect(title).to_have_text(original_name)

    edit_button.click()
    title_input.fill("Renamed set")
    title_input.press("Enter")
    expect(title_input).to_be_hidden()
    expect(title).to_have_text("Renamed set")
    expect(edit_button).to_be_visible()
