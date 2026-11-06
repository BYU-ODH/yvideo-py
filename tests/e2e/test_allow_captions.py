import pytest

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.django_db(transaction=True),
]

# The demo admin can edit the seeded content, so the server still sends it the subtitle tracks
# when captions are disallowed; these tests check that the player itself keeps them off.
_CAPTION_STATE = """() => {
    const video = document.querySelector('.annotation-player-container video');
    const captionsBtn = document.querySelector('.captions-btn-wrapper');
    const sidebarBtn = document.querySelector('.subtitle-sidebar-btn');
    return {
        trackCount: video.textTracks.length,
        captionsBtnVisible: !!captionsBtn && captionsBtn.style.display !== 'none',
        sidebarBtnVisible: !!sidebarBtn && sidebarBtn.style.display !== 'none',
    };
}"""


def _set_allow_captions(content, allowed):
    content.allow_captions = allowed
    content.save()


def _caption_state_once_tracks_settle(page):
    # Track loading finishes on a short timeout after loadedmetadata.
    page.wait_for_timeout(500)
    return page.evaluate(_CAPTION_STATE)


def test_player_offers_captions_when_they_are_allowed(page, open_player, demo_content):
    _set_allow_captions(demo_content, True)
    open_player(demo_content)
    page.wait_for_function(
        "() => document.querySelector('.annotation-player-container video').textTracks.length > 0"
    )

    state = _caption_state_once_tracks_settle(page)

    assert state["captionsBtnVisible"]
    assert state["sidebarBtnVisible"]


def test_player_hides_captions_when_they_are_disallowed(
    page, open_player, demo_content
):
    _set_allow_captions(demo_content, False)
    open_player(demo_content)

    state = _caption_state_once_tracks_settle(page)

    assert state["trackCount"] == 0
    assert not state["captionsBtnVisible"]
    assert not state["sidebarBtnVisible"]


def test_editor_still_loads_subtitles_when_captions_are_disallowed(
    page, open_editor, demo_content
):
    _set_allow_captions(demo_content, False)
    open_editor(demo_content)

    page.wait_for_function(
        "() => document.querySelector('.annotation-player-container video').textTracks.length > 0"
    )
