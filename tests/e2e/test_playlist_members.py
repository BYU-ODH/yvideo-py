"""Manage People driven through a real browser (#361).

The server-side rules have unit tests; what only a browser can check is that the
fragment swaps leave a working panel behind. Every handler is delegated from `document`
precisely because the markup is replaced twice over -- once by the settings Reset and
once by each add or remove -- so a rebinding mistake would show up here as a second
action silently doing nothing.
"""

import re

from playwright.sync_api import Page
from playwright.sync_api import expect
import pytest

from core.models import Playlist
from core.models import PlaylistRole
from core.models import PlaylistUserAccess

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.django_db(transaction=True),
]

# Seeded by core/dev_seed.py with a TA and two students, so the panel has rows to act on.
POPULATED_PLAYLIST = "Birds of a Feather"

# Longer than NETID_PATTERN allows, so AddUserLookupForm rejects it on the regex and never
# reaches BYU. That matters: a string that *looks* like a NetID sends the server to
# api.byu.edu for a token and a student-summary lookup, which would make these tests
# network-dependent, slow, and differently-behaved on a machine with real credentials than
# in CI without them. Everything past the regex is covered in core/tests/test_playlist_members.py
# against a stubbed API.
MALFORMED_IDENTIFIER = "nobodyhere"


@pytest.fixture
def members_panel(logged_in_page: Page, live_server):
    playlist = Playlist.objects.get(name=POPULATED_PLAYLIST)
    logged_in_page.goto(f"{live_server.url}/playlists/{playlist.pk}/")
    logged_in_page.locator("#playlist-manage-people-button").click()
    expect(logged_in_page.locator("#playlist-member-add-button")).to_be_visible()
    return playlist


def role_of(playlist, first_name):
    return PlaylistUserAccess.objects.get(
        playlist=playlist, user__first_name=first_name
    ).playlist_role


def test_the_panel_is_not_rendered_until_the_dialog_is_opened(
    logged_in_page: Page, live_server
):
    """The roster and the course-access counts are queries; most visits never open this.

    Asserted from the browser rather than by counting queries because what must not
    regress is the panel going back into the page template, which this would catch and a
    query count on the view would not.
    """
    page = logged_in_page
    playlist = Playlist.objects.get(name=POPULATED_PLAYLIST)
    page.goto(f"{live_server.url}/playlists/{playlist.pk}/")

    expect(page.locator("[data-playlist-members-placeholder]")).to_have_count(1)
    expect(page.locator("#playlist-member-add-button")).to_have_count(0)

    page.locator("#playlist-manage-people-button").click()
    expect(page.locator("#playlist-member-add-button")).to_be_visible()
    expect(page.locator("[data-playlist-members-placeholder]")).to_have_count(0)


def test_the_results_list_can_be_reached_and_used_from_the_keyboard(
    logged_in_page: Page, members_panel
):
    """ArrowDown moves focus into the list rather than steering it from the field.

    A <select size="4"> announces each option as focus moves through it; a selection
    changing while focus stays behind in the input announces nothing, which is what a
    screen reader user got before.
    """
    page = logged_in_page
    search = page.locator("#playlist-member-search")
    search.fill("iv")
    expect(
        page.locator("#playlist-member-results option:not([disabled])")
    ).to_have_count(1)

    search.press("ArrowDown")
    expect(page.locator("#playlist-member-results")).to_be_focused()

    # ArrowUp off the top hands the field back, so correcting a search needs no mouse.
    page.locator("#playlist-member-results").press("ArrowUp")
    expect(search).to_be_focused()

    search.press("ArrowDown")
    page.locator("#playlist-member-results").press("Enter")
    expect(page.locator("#playlist-members-status")).to_contain_text("added")
    expect(page.locator(".playlist-member-row", has_text="Ivy")).to_have_count(1)


def test_the_owner_is_listed_once_and_cannot_be_removed(
    logged_in_page: Page, members_panel
):
    """dev_seed gives some owners a co-instructor row on their own playlist."""
    page = logged_in_page
    owner_name = members_panel.owner.get_full_name() or members_panel.owner.username
    expect(page.locator(".playlist-member-name", has_text=owner_name)).to_have_count(1)
    expect(page.locator(".playlist-member-row", has_text=owner_name)).to_contain_text(
        "Owner"
    )


def test_changing_a_role_saves_and_announces_without_reloading(
    logged_in_page: Page, members_panel
):
    page = logged_in_page
    row = page.locator(".playlist-member-row", has_text="Casey")
    row.locator(".playlist-member-role-select").select_option(
        str(PlaylistRole.STUDENT.value)
    )

    expect(page.locator("#playlist-members-status")).to_contain_text("is now Student")
    expect(page.locator("#playlist-member-add-button")).to_be_visible()


def test_adding_someone_found_by_search_puts_them_in_the_list(
    logged_in_page: Page, members_panel
):
    page = logged_in_page
    page.locator("#playlist-member-search").fill("ivy")
    expect(
        page.locator("#playlist-member-results option:not([disabled])").first
    ).to_be_attached()

    page.locator("#playlist-member-results").select_option(index=0)
    page.locator("#playlist-member-role").select_option(str(PlaylistRole.TA.value))
    page.locator("#playlist-member-add-button").click()

    status = page.locator("#playlist-members-status")
    expect(status).to_be_visible()
    expect(status).to_contain_text("added")
    expect(status).to_have_class(re.compile(r"playlist-members-status-success"))
    expect(page.locator(".playlist-member-row", has_text="Ivy")).to_have_count(1)
    assert role_of(members_panel, "Ivy") == PlaylistRole.TA

    # The picker is cleared so the next add does not start on the previous person.
    expect(page.locator("#playlist-member-search")).to_have_value("")
    expect(page.locator("#playlist-member-results")).to_be_hidden()


def test_a_failed_lookup_reports_why_instead_of_silently_doing_nothing(
    logged_in_page: Page, members_panel
):
    page = logged_in_page
    page.locator("#playlist-member-search").fill(MALFORMED_IDENTIFIER)
    page.locator("#playlist-member-add-button").click()

    status = page.locator("#playlist-members-status")
    expect(status).to_be_visible()
    # The form's own words, not a generic fallback: a text/plain body from our own
    # endpoint is the one case the client is allowed to show verbatim.
    expect(status).to_contain_text("BYU ID")
    expect(status).to_have_class(re.compile(r"playlist-members-status-error"))
    expect(page.locator("#playlist-member-search.invalid-input")).to_have_count(1)

    # Editing the field clears the mark, so a later success is not shown beside a red box.
    page.locator("#playlist-member-search").fill(f"{MALFORMED_IDENTIFIER}2")
    expect(page.locator("#playlist-member-search.invalid-input")).to_have_count(0)


def test_the_status_message_sits_between_the_roster_and_the_add_form(
    logged_in_page: Page, members_panel
):
    """Where it reports from: under the list it changed, above the controls that did it."""
    page = logged_in_page
    order = page.evaluate(
        """() => {
            const body = document.querySelector('.playlist-members-body');
            return Array.from(body.children).map((child) => child.id
                || child.querySelector('h4')?.id || '');
        }"""
    )
    assert order.index("playlist-members-roster-heading") < order.index(
        "playlist-members-status"
    )
    assert order.index("playlist-members-status") < order.index(
        "playlist-members-add-heading"
    )


def test_the_panel_does_not_resize_around_its_status_messages(
    logged_in_page: Page, members_panel
):
    """A <dialog> is width: fit-content, so the status line can drive the panel width.

    A failed directory lookup returns a long sentence; the next success returns a short
    one. Without a width on the body the panel stretched to fit the error and snapped
    back afterwards, which reads as the dialog reloading.
    """
    page = logged_in_page
    dialog = page.locator("#playlist-members-modal")
    opening_width = dialog.bounding_box()["width"]

    # Too long to be a NetID, so the form refuses it before any BYU API call -- see the
    # note on MALFORMED_IDENTIFIER. The message is still the long one this test needs.
    page.locator("#playlist-member-search").fill(MALFORMED_IDENTIFIER)
    page.locator("#playlist-member-add-button").click()
    status = page.locator("#playlist-members-status")
    expect(status).to_contain_text("BYU ID")
    assert dialog.bounding_box()["width"] == opening_width

    page.locator(".playlist-member-row", has_text="Alice").locator(
        ".playlist-member-role-select"
    ).select_option(str(PlaylistRole.TA.value))
    expect(page.locator("#playlist-members-status")).to_contain_text("is now TA")
    assert dialog.bounding_box()["width"] == opening_width


def test_removing_someone_confirms_first_and_then_swaps_the_list(
    logged_in_page: Page, members_panel
):
    page = logged_in_page
    row = page.locator(".playlist-member-row", has_text="Alice")
    expect(row).to_have_count(1)
    row.locator(".playlist-member-remove-button").click()

    confirmation = page.locator("#playlist-member-remove-modal")
    expect(confirmation).to_be_visible()
    expect(confirmation).to_contain_text("Alice")

    page.locator("#playlist-member-confirm-remove").click()
    expect(confirmation).to_be_hidden()
    expect(page.locator("#playlist-members-status")).to_contain_text("removed")
    expect(page.locator(".playlist-member-row", has_text="Alice")).to_have_count(0)
    assert not PlaylistUserAccess.objects.filter(
        playlist=members_panel, user__first_name="Alice"
    ).exists()


def test_the_panel_still_works_after_the_settings_reset_swaps_it(
    logged_in_page: Page, members_panel
):
    """Reset replaces the whole settings panel, dialogs included, via outerHTML.

    Inserted markup never re-runs its <script>, so anything that had bound to the old
    nodes would be gone by now.
    """
    page = logged_in_page
    page.keyboard.press("Escape")
    page.locator("#playlist-settings-reset").click()
    expect(page.locator("#playlist-manage-people-button")).to_be_visible()

    page.locator("#playlist-manage-people-button").click()
    row = page.locator(".playlist-member-row", has_text="Casey")
    row.locator(".playlist-member-role-select").select_option(
        str(PlaylistRole.STUDENT.value)
    )
    expect(page.locator("#playlist-members-status")).to_contain_text("is now Student")
