import re

from playwright.sync_api import expect
import pytest

from core.models import Content

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.django_db(transaction=True),
]


def test_clips_only_checkbox_shows_warning_live_when_no_clips_are_defined(
    logged_in_page, live_server
):
    page = logged_in_page
    # "Birds Draft Discussion" shares the real birds.mp4 file but has no
    # annotation_set at all, so it has zero clips.
    content = Content.objects.get(title="Birds Draft Discussion")

    page.goto(f"{live_server.url}/content/{content.pk}/display-settings/")

    checkbox = page.locator("#clips-only")
    warning = page.locator("#clips-only-warning")
    form_group = page.locator("#clips-only-form-group")

    expect(checkbox).not_to_be_checked()
    expect(warning).to_be_hidden()

    checkbox.check()

    expect(warning).to_be_visible()
    expect(warning).to_contain_text("This video has no clips defined")
    expect(form_group).to_have_class(re.compile(r"clips-only-invalid"))

    checkbox.uncheck()

    expect(warning).to_be_hidden()
    expect(form_group).not_to_have_class(re.compile(r"clips-only-invalid"))


def test_clips_only_info_popover_opens_beside_its_button_and_dismisses(
    logged_in_page, live_server
):
    page = logged_in_page
    content = Content.objects.get(title="Birds Overview")
    page.goto(f"{live_server.url}/content/{content.pk}/display-settings/")

    info_button = page.locator("#clips-only-info-button")
    popover = page.locator("#clips-only-info")

    expect(popover).to_be_hidden()
    info_button.click()
    expect(popover).to_be_visible()
    expect(popover).to_contain_text("Fair Use")

    button_box = info_button.bounding_box()
    popover_box = popover.bounding_box()
    assert button_box
    assert popover_box
    assert popover_box["y"] >= button_box["y"] + button_box["height"]
    assert abs(popover_box["x"] - button_box["x"]) < 50

    page.keyboard.press("Escape")
    expect(popover).to_be_hidden()

    info_button.click()
    expect(popover).to_be_visible()
    page.mouse.click(1, 1)
    expect(popover).to_be_hidden()

    # Reset swaps in a freshly rendered form, so the new button must still open the new popover.
    page.locator("#content-settings-reset-button").click()
    page.locator("#clips-only-info-button").click()
    expect(page.locator("#clips-only-info")).to_be_visible()
