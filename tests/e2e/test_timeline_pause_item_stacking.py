"""A pause item's stacking footprint follows the zoom it is measured at.

A pause annotation is a point in time, so it has no width to draw. It renders shrink-to-fit at a
fixed pixel size instead, and the row-stacking pass - which otherwise works entirely in seconds -
needs *some* end time for it, so placeTrackItems converts those pixels into a duration:
`(itemWidth / containerWidth) * duration`.

That conversion is only true at the zoom level it was measured at. The timeline is
`100 * zoomLevel` percent wide, so zooming in leaves the pause item the same number of pixels
across a much wider container - its footprint in seconds shrinks by the same factor. Cached from an
earlier zoom, it holds items apart on lower rows that no longer overlap it (or, zooming out, lets
them collide). Recomputing it whenever the zoom changes is what keeps it honest.

Cosmetic on its own. Included because it is the same mistake as the resize floor and the scroll
offset: a quantity in seconds derived from a measurement whose reference keeps moving.
"""

import pytest

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.django_db(transaction=True),
]

PAUSE_AT_SECONDS = 5.0
ZOOM_CLICKS = 4
ZOOM_FACTOR = 1.5**ZOOM_CLICKS

ITEM_SELECTOR = '.track-item[data-annotation-type="pause"]'


@pytest.fixture
def editor_with_a_pause(open_editor, demo_content, page):
    """The seeded editor, plus a pause annotation on the demo content's first track."""
    from core.models import PauseAnnotation

    track = demo_content.annotation_set.get_tracks()[0]
    PauseAnnotation.objects.create(
        track=track,
        name="Pause For Discussion",
        start_time=PAUSE_AT_SECONDS,
        end_time=PAUSE_AT_SECONDS,
    )

    page.set_viewport_size({"width": 1400, "height": 900})
    open_editor()
    page.wait_for_selector(ITEM_SELECTOR)
    return demo_content


def _footprint_seconds(page):
    """The pause item's stacking footprint, and the pixel width it was derived from."""
    return page.evaluate(
        """(selector) => {
            const item = document.querySelector(selector);
            return {
                seconds: parseFloat(item.dataset.virtualEnd) - parseFloat(item.dataset.start),
                pixels: item.getBoundingClientRect().width,
            };
        }""",
        ITEM_SELECTOR,
    )


def test_zooming_in_shrinks_the_pause_footprint_by_the_zoom_factor(
    editor_with_a_pause, page
):
    before = _footprint_seconds(page)
    assert before["seconds"] > 0, "no footprint was computed, so this proves nothing"

    for _ in range(ZOOM_CLICKS):
        page.locator("#zoom-in-button").click()
    page.wait_for_timeout(300)
    after = _footprint_seconds(page)

    # The item is shrink-to-fit, so zoom does not change its pixel width - only what those pixels
    # are worth in seconds.
    assert after["pixels"] == pytest.approx(before["pixels"], abs=2), (
        "the pause item changed size on screen, so this test is measuring something else"
    )
    assert after["seconds"] == pytest.approx(
        before["seconds"] / ZOOM_FACTOR, rel=0.2
    ), (
        f"the footprint stayed at {after['seconds']:.2f}s after zooming {ZOOM_FACTOR:.1f}x, "
        f"where the item's pixels are now worth {before['seconds'] / ZOOM_FACTOR:.2f}s - it is "
        "being held from the zoom level it was first measured at"
    )


def test_zooming_back_out_restores_the_original_footprint(editor_with_a_pause, page):
    before = _footprint_seconds(page)

    for _ in range(ZOOM_CLICKS):
        page.locator("#zoom-in-button").click()
    page.wait_for_timeout(200)
    for _ in range(ZOOM_CLICKS):
        page.locator("#zoom-out-button").click()
    page.wait_for_timeout(300)

    after = _footprint_seconds(page)
    # Exact, not approximate: both measurements come from the container's own box at zoom 1, so a
    # round trip has nothing left to drift. It used to land ~11% off, because the reference was the
    # children's scrollWidth - a number that moved as the items around it were repositioned.
    assert after["seconds"] == pytest.approx(before["seconds"], rel=0.01)
