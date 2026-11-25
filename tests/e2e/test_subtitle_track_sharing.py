import pytest

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.django_db(transaction=True),
]


def _open_subtitle_menu(page):
    page.locator("#subtitle-track-menu-button").click()
    page.wait_for_selector("#subtitle-track-settings-menu", state="visible")


def test_a_track_can_be_removed_from_a_video_and_added_back(
    logged_in_page, open_editor, demo_content
):
    page = logged_in_page
    track = demo_content.subtitle_tracks.order_by("name").first()
    open_editor(demo_content)
    page.locator("#annotation-panel-switch").click()

    page.select_option("#subtitles-track-selector", str(track.pk))
    page.wait_for_selector(".editor-subtitle-cue")
    _open_subtitle_menu(page)
    with page.expect_navigation():
        page.locator("#remove-subtitle-track-button").click()
    open_editor(demo_content)

    assert not demo_content.subtitle_tracks.filter(pk=track.pk).exists()
    assert page.locator(f'.subtitles-track-option[value="{track.pk}"]').count() == 0

    page.locator("#annotation-panel-switch").click()
    _open_subtitle_menu(page)
    page.locator("#open-add-subtitle-track-modal").click()
    page.select_option("#add-subtitle-track-selector", str(track.pk))
    with page.expect_navigation():
        page.locator("#add-subtitle-track-button").click()
    page.wait_for_selector(".editor-subtitle-cue")

    assert demo_content.subtitle_tracks.filter(pk=track.pk).exists()
    assert page.locator("#subtitles-track-selector").input_value() == str(track.pk)


def test_deleting_a_track_takes_it_off_the_video(
    logged_in_page, open_editor, demo_content
):
    page = logged_in_page
    track = demo_content.subtitle_tracks.order_by("name").first()
    open_editor(demo_content)
    page.locator("#annotation-panel-switch").click()

    page.select_option("#subtitles-track-selector", str(track.pk))
    page.wait_for_selector(".editor-subtitle-cue")
    _open_subtitle_menu(page)
    page.locator("#open-delete-subtitle-track-modal").click()
    with page.expect_navigation():
        page.locator("#delete-subtitle-track-confirm").click()

    assert not demo_content.subtitle_tracks.filter(pk=track.pk).exists()
