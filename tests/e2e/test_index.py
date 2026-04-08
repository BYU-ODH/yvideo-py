from playwright.sync_api import expect
import pytest

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.django_db(transaction=True),
]


def test_demo_admin_can_load_index_page(page, live_server, seeded_demo_data):
    response = page.goto(f"{live_server.url}/login/dev/quick/")

    assert response is not None
    assert response.ok
    expect(page).to_have_url(f"{live_server.url}/")
    expect(page.get_by_role("heading", name="My Collections")).to_be_visible()
