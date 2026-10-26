from contextlib import contextmanager
import re

from playwright.sync_api import expect
import pytest

from core.models import CommentAnnotation

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.django_db(transaction=True),
]

SEEDED_COMMENT = "Bird Notes 1"


def _open_comment(page, open_editor):
    content = open_editor()
    annotation = CommentAnnotation.objects.get(
        name=SEEDED_COMMENT,
        track__annotation_set=content.annotation_set,
        active=True,
    )
    page.locator(f"#comment-{annotation.id} .track-item-content").click()
    expect(page.locator("#existing-item-form")).to_have_attribute(
        "data-annotation-id", str(annotation.id)
    )
    # The comment inputs only save while the player is drawing the box.
    expect(page.locator(f"#comment-text-box-{annotation.id}")).to_be_visible()
    return content, annotation


@contextmanager
def _expect_saved_color(page, content, expected):
    # Register before editing so even a fast autosave cannot escape the wait.
    # Finish the player-data refresh too, before querying SQLite or tearing down.
    with page.expect_response(f"**/content/{content.pk}/player-data/") as refresh_info:
        with page.expect_response(
            lambda response: (
                f"/content/{content.pk}/annotations/comment/" in response.url
                and response.url.endswith("/update/")
                and response.request.method == "POST"
                and response.request.post_data_json.get("font_color") == expected
            )
        ) as save_info:
            yield
        save_response = save_info.value
        save_response.finished()
        assert save_response.ok, f"Color save failed: HTTP {save_response.status}"
    refresh_response = refresh_info.value
    refresh_response.finished()
    assert refresh_response.ok, (
        f"Player data refresh failed: HTTP {refresh_response.status}"
    )
    saved_colors = set(
        CommentAnnotation.objects.filter(
            track__annotation_set=content.annotation_set,
            name=SEEDED_COMMENT,
            active=True,
        ).values_list("font_color", flat=True)
    )
    assert expected in saved_colors


def test_choosing_a_preset_color_from_the_list(page, open_editor):
    content, annotation = _open_comment(page, open_editor)
    color_input = page.locator("#font-color")
    options = page.locator("#font-color-options")

    expect(options).to_be_hidden()
    page.locator(".color-combobox-toggle").click()
    expect(options).to_be_visible()
    expect(color_input).to_have_attribute("aria-expanded", "true")
    # Every preset is offered, not only the ones matching the current value.
    expect(options.get_by_role("option")).to_have_count(8)

    with _expect_saved_color(page, content, "d32f2f"):
        options.get_by_role("option", name="Red").click()
        expect(options).to_be_hidden()
        expect(color_input).to_have_value("d32f2f")
        expect(page.locator(f"#comment-text-box-{annotation.id}")).to_have_css(
            "color", "rgb(211, 47, 47)"
        )


def test_choosing_a_preset_color_with_the_keyboard(page, open_editor):
    content, _ = _open_comment(page, open_editor)
    color_input = page.locator("#font-color")
    with _expect_saved_color(page, content, "000000"):
        color_input.fill("000000")

    color_input.press("ArrowDown")
    expect(page.locator("#font-color-options")).to_be_visible()
    # The list opens on the current color (Black), so one more step lands on White.
    color_input.press("ArrowDown")
    expect(color_input).to_have_attribute(
        "aria-activedescendant", "font-color-option-ffffff"
    )
    color_input.press("Escape")
    expect(page.locator("#font-color-options")).to_be_hidden()

    color_input.press("ArrowDown")
    color_input.press("ArrowUp")
    with _expect_saved_color(page, content, "f9a825"):
        color_input.press("Enter")
        expect(color_input).to_have_value("f9a825")


def test_typing_a_custom_hex_code_with_a_leading_hash(page, open_editor):
    content, _ = _open_comment(page, open_editor)
    color_input = page.locator("#font-color")
    color_input.fill("")
    with _expect_saved_color(page, content, "123abc"):
        color_input.press_sequentially("#123abc")
        expect(color_input).to_have_value("123abc")


def test_arrow_up_from_a_custom_color_starts_at_the_last_preset(page, open_editor):
    _open_comment(page, open_editor)
    color_input = page.locator("#font-color")
    expect(color_input).to_have_value("aaaaaa")

    color_input.press("ArrowDown")
    expect(color_input).not_to_have_attribute("aria-activedescendant", re.compile(".*"))
    color_input.press("ArrowUp")
    expect(color_input).to_have_attribute(
        "aria-activedescendant", "font-color-option-f9a825"
    )


def test_the_swatch_clears_while_the_hex_code_is_incomplete(page, open_editor):
    content, _ = _open_comment(page, open_editor)
    color_input = page.locator("#font-color")
    swatch = page.locator(".color-combobox > .color-swatch")

    color_input.fill("12")
    expect(swatch).to_have_css("background-color", "rgba(0, 0, 0, 0)")
    with _expect_saved_color(page, content, "123"):
        color_input.fill("123")
        expect(swatch).to_have_css("background-color", "rgb(17, 34, 51)")
