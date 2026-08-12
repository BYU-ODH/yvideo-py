from playwright.sync_api import expect
import pytest

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.django_db(transaction=True),
]


def test_demo_admin_lands_on_playlists(page, live_server, seeded_demo_data):
    response = page.goto(f"{live_server.url}/login/dev/quick/")

    assert response is not None
    assert response.ok
    expect(page).to_have_url(f"{live_server.url}/playlists/")
    expect(page.get_by_role("heading", name="My Playlists")).to_be_visible()


def test_the_whats_new_banner_dismisses_and_stays_dismissed(
    logged_in_page, live_server
):
    """Asserts visibility, not the hidden attribute: the banner's own `display: flex`
    outranks the browser's [hidden] rule, so the attribute alone proves nothing."""
    page = logged_in_page
    banner = page.locator("#whats-new-banner")
    expect(banner).to_be_visible()

    page.locator("#whats-new-dismiss").click()
    expect(banner).to_be_hidden()

    page.goto(f"{live_server.url}/playlists/")
    expect(page.get_by_role("heading", name="My Playlists")).to_be_visible()
    expect(banner).to_be_hidden()
