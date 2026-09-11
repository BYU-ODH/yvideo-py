import re

from playwright.sync_api import Page
from playwright.sync_api import expect
import pytest

from core.models import Content
from core.models import Playlist

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.django_db(transaction=True),
]


def assert_shared_modal_shell_and_dismissal(
    page: Page, selector: str, opener_selector: str | None
):
    dialog = page.locator(selector)
    expect(dialog).to_have_count(1)
    expect(dialog.locator(":scope > .user-view-modal-header")).to_have_count(1)
    expect(dialog.locator(":scope > .user-view-modal-body")).to_have_count(1)

    labelled_by = dialog.get_attribute("aria-labelledby")
    assert labelled_by
    expect(dialog.locator(f"#{labelled_by}")).to_have_count(1)

    if opener_selector:
        page.locator(opener_selector).first.click()
    else:
        dialog.evaluate("element => element.showModal()")
    expect(dialog).to_be_visible()

    close_button = dialog.get_by_role("button", name="Close")
    expect(close_button).to_have_text("X")
    dialog_box = dialog.bounding_box()
    close_button_box = close_button.bounding_box()
    assert dialog_box
    assert close_button_box
    assert close_button_box["x"] > dialog_box["x"] + dialog_box["width"] / 2
    assert close_button_box["y"] < dialog_box["y"] + dialog_box["height"] / 2

    close_button.click()
    expect(dialog).to_be_hidden()

    dialog.evaluate("element => element.showModal()")
    expect(dialog).to_be_visible()
    page.mouse.click(1, 1)
    expect(dialog).to_be_hidden()


def assert_header_controls_do_not_overlap(dialog):
    title_box = dialog.locator(":scope > .user-view-modal-header h3").bounding_box()
    back_button_box = dialog.get_by_role("button", name="Back").bounding_box()
    close_button_box = dialog.get_by_role("button", name="Close").bounding_box()
    assert title_box
    assert back_button_box
    assert close_button_box
    assert back_button_box["x"] + back_button_box["width"] <= title_box["x"]
    assert close_button_box["x"] >= title_box["x"] + title_box["width"]


def test_every_user_view_dialog_uses_the_shared_shell_and_behavior(
    logged_in_page: Page, live_server
):
    playlist = Playlist.objects.get(name="Demo Review Shelf")
    content = Content.objects.get(title="Birds Overview")
    pages_and_dialogs = [
        (
            "/playlists/",
            [("#add-new-playlist-dialog", "#add-new-playlist-button")],
        ),
        (
            f"/playlists/{playlist.pk}/",
            [
                ("#playlist-delete-modal", "#playlist-settings-delete"),
                (
                    "#playlist-course-assignments-modal",
                    "#playlist-edit-course-assignment-button",
                ),
                (
                    "#add-video-dialog",
                    "[commandfor='add-video-dialog'][command='show-modal']",
                ),
                ("#select-resource-dialog", None),
                ("#create-from-resource-modal", None),
                ("#playlist-members-modal", "#playlist-manage-people-button"),
                ("#playlist-member-remove-modal", None),
            ],
        ),
        (
            f"/content/{content.pk}/display-settings/",
            [("#content-delete-modal", "#content-details-delete-button")],
        ),
    ]

    for path, dialogs in pages_and_dialogs:
        logged_in_page.goto(f"{live_server.url}{path}")
        for selector, opener_selector in dialogs:
            assert_shared_modal_shell_and_dismissal(
                logged_in_page, selector, opener_selector
            )


def test_existing_resource_modal_chain_stays_on_the_playlist_page(
    logged_in_page: Page, live_server
):
    playlist = Playlist.objects.get(name="Demo Review Shelf")
    playlist_url = f"{live_server.url}/playlists/{playlist.pk}/"
    logged_in_page.goto(playlist_url)

    add_video_modal = logged_in_page.locator("#add-video-dialog")
    resource_picker_modal = logged_in_page.locator("#select-resource-dialog")
    resource_form_modal = logged_in_page.locator("#create-from-resource-modal")

    logged_in_page.locator(
        "[commandfor='add-video-dialog'][command='show-modal']"
    ).click()
    expect(add_video_modal).to_be_visible()

    add_video_modal.locator("#add-from-resource-button").click()
    expect(add_video_modal).to_be_hidden()
    expect(resource_picker_modal).to_be_visible()
    expect(logged_in_page).to_have_url(playlist_url)

    resource_picker_modal.get_by_role("button", name="Back").click()
    expect(resource_picker_modal).to_be_hidden()
    expect(add_video_modal).to_be_visible()

    add_video_modal.locator("#add-from-resource-button").click()
    expect(resource_picker_modal).to_be_visible()
    resource_button = resource_picker_modal.get_by_role(
        "button", name=re.compile(r"^Birds")
    )
    resource_button.focus()
    logged_in_page.keyboard.press("Enter")
    expect(resource_picker_modal).to_be_hidden()
    expect(resource_form_modal).to_be_visible()
    assert_header_controls_do_not_overlap(resource_form_modal)
    expect(logged_in_page).to_have_url(playlist_url)

    resource_form_modal.get_by_role("button", name="Back").click()
    expect(resource_form_modal).to_be_hidden()
    expect(resource_picker_modal).to_be_visible()

    resource_button.click()
    expect(resource_form_modal).to_be_visible()
    resource_form_modal.locator("#content-title-input").fill("Added in modal chain")
    resource_form_modal.locator("#resource-file-input").select_option(index=1)

    with logged_in_page.expect_navigation():
        resource_form_modal.get_by_role("button", name="Create").click()

    expect(logged_in_page).to_have_url(playlist_url)
    expect(
        logged_in_page.locator(".playlist-video .video-title").filter(
            has_text="Added in modal chain"
        )
    ).to_have_count(1)


def test_canceling_pending_resource_requests_does_not_advance_the_modal_chain(
    logged_in_page: Page, live_server
):
    playlist = Playlist.objects.get(name="Demo Review Shelf")
    logged_in_page.goto(f"{live_server.url}/playlists/{playlist.pk}/")

    add_video_modal = logged_in_page.locator("#add-video-dialog")
    resource_picker_modal = logged_in_page.locator("#select-resource-dialog")
    resource_form_modal = logged_in_page.locator("#create-from-resource-modal")
    open_add_video_button = logged_in_page.locator(
        "[commandfor='add-video-dialog'][command='show-modal']"
    )

    resource_list_url = (
        f"{live_server.url}/playlists/{playlist.pk}/create-from-resource/resources/"
    )
    held_resource_list_routes = []

    def hold_resource_list(route):
        held_resource_list_routes.append(route)

    logged_in_page.route(resource_list_url, hold_resource_list)
    open_add_video_button.click()
    with logged_in_page.expect_request(resource_list_url):
        add_video_modal.locator("#add-from-resource-button").click()
    assert len(held_resource_list_routes) == 1

    add_video_modal.get_by_role("button", name="Close").click()
    held_resource_list_routes[0].fulfill(
        status=200,
        content_type="text/html",
        body='<button id="stale-resource-list-response"></button>',
    )
    logged_in_page.wait_for_timeout(100)
    expect(resource_picker_modal).to_be_hidden()
    expect(logged_in_page.locator("#stale-resource-list-response")).to_have_count(0)
    logged_in_page.unroute(resource_list_url, hold_resource_list)

    open_add_video_button.click()
    add_video_modal.locator("#add-from-resource-button").click()
    expect(resource_picker_modal).to_be_visible()

    resource_button = resource_picker_modal.locator(".resource-details").first
    resource_id = resource_button.get_attribute("data-resource-id")
    assert resource_id
    resource_form_url = (
        f"{live_server.url}/playlists/{playlist.pk}/create-from-resource/"
        f"{resource_id}/form/"
    )
    held_resource_form_routes = []

    def hold_resource_form(route):
        held_resource_form_routes.append(route)

    logged_in_page.route(resource_form_url, hold_resource_form)
    with logged_in_page.expect_request(resource_form_url):
        resource_button.click()
    assert len(held_resource_form_routes) == 1

    resource_picker_modal.get_by_role("button", name="Back").click()
    held_resource_form_routes[0].fulfill(
        status=200,
        content_type="text/html",
        body="""
            <div id="create-from-resource-form">
                <input id="playlist-id" value="1">
                <input id="content-title-input">
                <select id="resource-file-input"><option value="1"></option></select>
                <button id="create-from-resource-form-submit"></button>
                <span id="stale-resource-form-response"></span>
            </div>
        """,
    )
    logged_in_page.wait_for_timeout(100)
    expect(resource_form_modal).to_be_hidden()
    expect(add_video_modal).to_be_visible()
    expect(logged_in_page.locator("#stale-resource-form-response")).to_have_count(0)
