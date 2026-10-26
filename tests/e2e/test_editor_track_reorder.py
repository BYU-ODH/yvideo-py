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


def _track_names(page):
    return page.locator(".track-row .track-name").all_inner_texts()


def _click_menu(page, track, button_class):
    row = page.locator(f'.track-row[data-track-id="{track.id}"]')
    row.locator(".open-multi-select-button").click()
    row.locator(button_class).click()


@pytest.mark.parametrize(
    ("moved", "button_class", "expected_tail"),
    [
        ("Second", ".track-menu-move-down", ["Third", "Second"]),
        ("Third", ".track-menu-move-up", ["Third", "Second"]),
    ],
)
def test_moving_a_track_swaps_it_with_its_neighbor(
    page, open_editor, demo_content, moved, button_class, expected_tail
):
    tracks = {name: _add_track(demo_content, name) for name in ("Second", "Third")}
    open_editor()
    before = _track_names(page)

    _click_menu(page, tracks[moved], button_class)

    page.wait_for_function(
        "expected => [...document.querySelectorAll('.track-row .track-name')]"
        ".slice(-2).map(el => el.innerText).join() === expected",
        arg=",".join(expected_tail),
    )
    assert _track_names(page) == before[:-2] + expected_tail
    positions = {
        track.name: track.stack_position
        for track in Track.objects.filter(pk__in=[t.pk for t in tracks.values()])
    }
    assert positions["Third"] < positions["Second"]
