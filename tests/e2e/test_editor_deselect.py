from playwright.sync_api import expect
import pytest

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.django_db(transaction=True),
]

PLACEHOLDER = ".editor-detail-form-placeholder"
EMPTY_PANEL_SPACE = (
    "#editor-annotation-panel, #annotation-groups-wrapper, .annotation-type-wrapper"
)


def _select_first(page, annotation_type):
    page.locator(
        f'.track-item[data-annotation-type="{annotation_type}"] .track-item-content'
    ).first.click()
    expect(page.locator("#existing-item-form")).to_be_visible()
    expect(page.locator(".active-track-item")).to_have_count(1)
    expect(page.locator(".active-panel-item")).to_have_count(1)


def _empty_point(page, box_selector, empty_selector):
    """A point within box_selector's box whose topmost element matches empty_selector,
    so the click lands on bare container space rather than on anything inside it."""
    point = page.evaluate(
        """([boxSelector, emptySelector]) => {
            const box = document.querySelector(boxSelector).getBoundingClientRect();
            for (let fy = 0.05; fy < 1; fy += 0.1) {
                for (let fx = 0.02; fx < 1; fx += 0.02) {
                    const x = box.left + box.width * fx;
                    const y = box.top + box.height * fy;
                    if (document.elementFromPoint(x, y)?.matches(emptySelector)) return {x, y};
                }
            }
            return null;
        }""",
        [box_selector, empty_selector],
    )
    assert point, f"no empty space found in {box_selector}"
    return point


def _click_empty_track_space(page):
    selector = ".track-row-annotations-container"
    point = _empty_point(page, selector, selector)
    page.mouse.click(point["x"], point["y"])


def _assert_deselected(page):
    expect(page.locator(PLACEHOLDER)).to_be_visible()
    expect(page.locator("#existing-item-form")).to_have_count(0)
    expect(page.locator(".active-track-item")).to_have_count(0)
    expect(page.locator(".active-panel-item")).to_have_count(0)


def test_clicking_empty_track_space_deselects(page, open_editor):
    open_editor()
    _select_first(page, "comment")
    expect(page.locator("#comment-edit-rig")).to_be_visible()

    _click_empty_track_space(page)

    _assert_deselected(page)
    expect(page.locator("#comment-edit-rig")).to_have_count(0)


def test_clicking_empty_annotation_panel_space_deselects(page, open_editor):
    open_editor()
    _select_first(page, "comment")

    point = _empty_point(page, "#editor-annotation-panel", EMPTY_PANEL_SPACE)
    page.mouse.click(point["x"], point["y"])

    _assert_deselected(page)


def test_deselecting_a_blur_removes_its_editing_box(page, open_editor):
    open_editor()
    _select_first(page, "blur")
    page.wait_for_selector("#blur-edit-rig", state="attached")
    expect(page.locator(".annotation-box-blur-editor")).to_have_count(1)

    _click_empty_track_space(page)

    _assert_deselected(page)
    expect(page.locator("#blur-edit-rig")).to_have_count(0)
    expect(page.locator(".annotation-box-blur-editor")).to_have_count(0)


def test_a_save_landing_after_deselection_does_not_reselect(page, open_editor):
    open_editor()
    _select_first(page, "comment")
    page.locator("#annotation_name").fill("Renamed before deselecting")

    # Clicking away blurs the field, which saves it; the response arrives after the deselect.
    with page.expect_response(lambda response: "/update/" in response.url):
        _click_empty_track_space(page)
    page.wait_for_timeout(300)

    _assert_deselected(page)
    expect(
        page.locator(".track-item", has_text="Renamed before deselecting")
    ).to_have_count(1)


def test_escape_deselects(page, open_editor):
    open_editor()
    _select_first(page, "comment")

    page.locator("body").press("Escape")

    _assert_deselected(page)
    expect(page.locator("#comment-edit-rig")).to_have_count(0)


def test_escape_in_a_text_field_keeps_the_selection(page, open_editor):
    open_editor()
    _select_first(page, "comment")

    page.locator("#annotation_name").press("Escape")

    expect(page.locator("#existing-item-form")).to_be_visible()
    expect(page.locator(".active-track-item")).to_have_count(1)
    expect(page.locator(".active-panel-item")).to_have_count(1)


def test_deleting_the_open_item_shows_the_placeholder(page, open_editor):
    open_editor()
    _select_first(page, "comment")

    page.once("dialog", lambda dialog: dialog.accept())
    page.locator("#annotation-form-delete-button").click()

    _assert_deselected(page)
    expect(page.locator("#comment-edit-rig")).to_have_count(0)


def test_clicking_a_panel_group_header_keeps_the_selection(page, open_editor):
    open_editor()
    _select_first(page, "comment")

    page.locator("#comment-annotation-type-header").click()

    expect(page.locator("#existing-item-form")).to_be_visible()
    expect(page.locator(".active-track-item")).to_have_count(1)
    expect(page.locator(".active-panel-item")).to_have_count(1)
