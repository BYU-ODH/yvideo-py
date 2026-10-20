from playwright.sync_api import expect
import pytest

from core.factories import MuteAnnotationFactory
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


def _click_delete(page, track):
    row = page.locator(f'.track-row[data-track-id="{track.id}"]')
    row.locator(".open-multi-select-button").click()
    row.locator(".track-menu-delete").click()
    return row


def test_deleting_a_track_with_items_asks_first(page, open_editor, demo_content):
    track = _add_track(demo_content, "Populated")
    MuteAnnotationFactory(track=track, start_time=1.0, end_time=2.0)
    MuteAnnotationFactory(track=track, start_time=3.0, end_time=4.0)
    open_editor()

    asked = []
    page.once("dialog", lambda dialog: (asked.append(dialog.message), dialog.dismiss()))
    row = _click_delete(page, track)

    expect(row).to_be_visible()
    assert asked and "2 annotations" in asked[0]
    assert Track.objects.filter(id=track.id).exists()

    page.once("dialog", lambda dialog: dialog.accept())
    row.locator(".track-menu-delete").click()

    expect(row).to_have_count(0)
    assert not Track.objects.filter(id=track.id).exists()


def test_deleting_an_empty_track_does_not_ask(page, open_editor, demo_content):
    track = _add_track(demo_content, "Empty")
    open_editor()

    asked = []
    page.on("dialog", lambda dialog: (asked.append(dialog.message), dialog.dismiss()))
    row = _click_delete(page, track)

    expect(row).to_have_count(0)
    assert not asked
