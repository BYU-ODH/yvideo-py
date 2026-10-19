from playwright.sync_api import expect
import pytest

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.django_db(transaction=True),
]

PLACEHOLDER = ".editor-detail-form-placeholder"


def _select_first_comment(page):
    page.locator(
        '.track-item[data-annotation-type="comment"] .track-item-content'
    ).first.click()
    expect(page.locator("#existing-item-form")).to_be_visible()
    expect(page.locator(".active-track-item")).to_have_count(1)
    expect(page.locator(".active-panel-item")).to_have_count(1)


def _empty_point(page, selector):
    """A point within the first selector's box whose topmost element matches the selector,
    so the click lands on bare container space rather than on anything inside it."""
    point = page.evaluate(
        """(selector) => {
            const box = document.querySelector(selector.split(",")[0]).getBoundingClientRect();
            for (let fy = 0.05; fy < 1; fy += 0.1) {
                for (let fx = 0.02; fx < 1; fx += 0.02) {
                    const x = box.left + box.width * fx;
                    const y = box.top + box.height * fy;
                    if (document.elementFromPoint(x, y)?.matches(selector)) return {x, y};
                }
            }
            return null;
        }""",
        selector,
    )
    assert point, f"no empty space found in {selector}"
    return point


def _assert_deselected(page):
    expect(page.locator(PLACEHOLDER)).to_be_visible()
    expect(page.locator("#existing-item-form")).to_have_count(0)
    expect(page.locator(".active-track-item")).to_have_count(0)
    expect(page.locator(".active-panel-item")).to_have_count(0)


def test_clicking_empty_track_space_deselects(page, open_editor):
    open_editor()
    _select_first_comment(page)

    point = _empty_point(page, ".track-row-annotations-container")
    page.mouse.click(point["x"], point["y"])

    _assert_deselected(page)


def test_clicking_empty_annotation_panel_space_deselects(page, open_editor):
    open_editor()
    _select_first_comment(page)

    point = _empty_point(
        page,
        "#editor-annotation-panel, #annotation-groups-wrapper, .annotation-type-wrapper",
    )
    page.mouse.click(point["x"], point["y"])

    _assert_deselected(page)


def test_clicking_a_panel_group_header_keeps_the_selection(page, open_editor):
    open_editor()
    _select_first_comment(page)

    page.locator("#comment-annotation-type-header").click()

    expect(page.locator("#existing-item-form")).to_be_visible()
    expect(page.locator(".active-track-item")).to_have_count(1)
