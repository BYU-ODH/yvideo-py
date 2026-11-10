"""The editor for a content whose annotation set belongs to someone else (#391).

Selecting an annotation is a read, so it opens the read-only form; trying to change one
offers to make the user their own copy instead of silently doing nothing.
"""

from playwright.sync_api import expect
import pytest

from tests.e2e.conftest import _EDITOR_READY
from tests.e2e.conftest import READY_TIMEOUT_MS
from tests.e2e.conftest import VIDEO_SELECTOR

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.django_db(transaction=True),
]

COPY_PROMPT = "#annotation-set-copy-prompt"
GRID_CALLOUT = '.track-item[data-annotation-type="comment"]'


@pytest.fixture
def borrowed_editor(logged_in_page, live_server, read_only_annotation_set_content):
    """The editor on the borrowed set, as Ada: an admin can edit any set, so spoofing is
    the only way to reach the read-only branch."""
    page = logged_in_page
    page.goto(f"{live_server.url}/playlists/")
    page.locator("#spoof-user-input").fill("Ada")
    page.wait_for_selector("#spoof-user-select option")
    value = page.locator("#spoof-user-select option").first.get_attribute("value")
    page.select_option("#spoof-user-select", value)
    page.locator("#spoof-user-submit").click()
    page.wait_for_selector("#spoof-warning")

    page.goto(f"{live_server.url}/video-editor/{read_only_annotation_set_content.pk}/")
    page.wait_for_function(_EDITOR_READY, arg=VIDEO_SELECTOR, timeout=READY_TIMEOUT_MS)
    return page


def test_clicking_a_timeline_item_opens_its_read_only_form(borrowed_editor):
    page = borrowed_editor

    page.locator(f"{GRID_CALLOUT} .track-item-content").click()

    form = page.locator("#annotation-update-form")
    expect(form).to_be_visible()
    expect(form.locator("#annotation_name")).to_have_attribute("readonly", "")
    expect(page.locator("#annotation-form-delete-button")).to_have_count(0)
    expect(page.locator(COPY_PROMPT)).to_have_count(0)


def test_resizing_a_timeline_item_offers_a_copy_and_leaves_it_in_place(borrowed_editor):
    page = borrowed_editor
    item = page.locator(GRID_CALLOUT)
    before = item.bounding_box()

    handle = item.locator(".resize-handle-right")
    handle.hover()
    page.mouse.down()
    page.mouse.move(before["x"] + before["width"] + 80, before["y"] + 5, steps=5)
    page.mouse.up()

    expect(page.locator(COPY_PROMPT)).to_be_visible()
    after = item.bounding_box()
    assert after["x"] == pytest.approx(before["x"], abs=1)
    assert after["width"] == pytest.approx(before["width"], abs=1)
