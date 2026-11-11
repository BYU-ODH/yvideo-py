from playwright.sync_api import expect
import pytest

from core.factories import TrackFactory
from core.models import Track

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.django_db(transaction=True),
]


def _add_track(content, name):
    annotation_set = content.annotation_set
    return TrackFactory(
        annotation_set=annotation_set,
        name=name,
        stack_position=annotation_set.get_highest_stack_position() + 1,
    )


def _open_rename(page, track):
    row = page.locator(f'.track-row[data-track-id="{track.id}"]')
    row.locator(".open-multi-option-menu").click()
    row.locator(".track-menu-rename").click()
    rename_input = row.locator(".track-rename-input")
    expect(rename_input).to_be_visible()
    expect(rename_input).to_be_focused()
    return row, rename_input


def test_enter_saves_the_trimmed_name_and_updates_labels(
    page, open_editor, demo_content
):
    track = _add_track(demo_content, "Before")
    open_editor()

    row, rename_input = _open_rename(page, track)
    rename_input.fill("  After  ")
    rename_input.press("Enter")

    expect(rename_input).to_be_hidden()
    expect(row.locator(".track-name")).to_have_text("After")
    expect(row.locator(".track-menu-rename")).to_have_attribute(
        "aria-label", "Rename track After"
    )
    expect(row.locator(".open-multi-option-menu")).to_have_attribute(
        "aria-label", "Show options menu for track After"
    )
    expect(row).to_have_attribute("data-track-name", "After")
    expect(page.locator("#annotation-save-status .save-status-text")).to_have_text(
        "Changes saved"
    )
    track.refresh_from_db()
    assert track.name == "After"


def test_the_page_shows_the_tracks_as_the_server_saved_them(
    page, open_editor, demo_content
):
    renamed = _add_track(demo_content, "Mine")
    elsewhere = _add_track(demo_content, "Old")
    open_editor()
    # Changed behind the editor's back, e.g. by a collaborator in another tab.
    Track.objects.filter(pk=elsewhere.pk).update(name="Changed elsewhere")

    row, rename_input = _open_rename(page, renamed)
    rename_input.fill("New")
    rename_input.press("Enter")

    expect(row.locator(".track-name")).to_have_text("New")
    other_row = page.locator(f'.track-row[data-track-id="{elsewhere.id}"]')
    expect(other_row.locator(".track-name")).to_have_text("Changed elsewhere")


@pytest.mark.parametrize("cancel", ["Escape", "blur"])
def test_escape_or_clicking_away_cancels(page, open_editor, demo_content, cancel):
    track = _add_track(demo_content, "Keep")
    open_editor()

    row, rename_input = _open_rename(page, track)
    rename_input.fill("Discard")
    if cancel == "Escape":
        rename_input.press("Escape")
    else:
        rename_input.evaluate("el => el.blur()")

    expect(rename_input).to_be_hidden()
    expect(row.locator(".track-name")).to_have_text("Keep")
    expect(rename_input).to_have_value("Keep")
    assert Track.objects.get(pk=track.pk).name == "Keep"


def test_a_failed_save_restores_the_name_and_reports_it(
    page, open_editor, demo_content
):
    track = _add_track(demo_content, "Original")
    open_editor()
    page.route("**/update-name/", lambda route: route.fulfill(status=500))

    row, rename_input = _open_rename(page, track)
    rename_input.fill("Lost")
    rename_input.press("Enter")

    expect(rename_input).to_be_hidden()
    expect(row.locator(".track-name")).to_have_text("Original")
    expect(page.locator("#annotation-save-status .save-status-text")).to_have_text(
        "Save failed"
    )


def test_a_network_error_still_closes_the_field(page, open_editor, demo_content):
    track = _add_track(demo_content, "Original")
    open_editor()
    page.route("**/update-name/", lambda route: route.abort())

    row, rename_input = _open_rename(page, track)
    rename_input.fill("Lost")
    rename_input.press("Enter")

    expect(rename_input).to_be_hidden()
    expect(row.locator(".track-name")).to_have_text("Original")
