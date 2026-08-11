from pathlib import Path

from playwright.sync_api import Page
import pytest

from core.models import Content
from core.models import Playlist

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.django_db(transaction=True),
]

# axe-core is pinned in package.json and installed via `npm ci`. Injecting it
# ourselves (rather than via a wrapper package) lets us control the axe-core
# version directly.
AXE_MIN_JS = (
    Path(__file__).resolve().parents[2] / "node_modules" / "axe-core" / "axe.min.js"
)

# The full-page views (those that extend base.html and render a complete
# document) are the meaningful axe targets. Everything else in urls.py is an
# HTMX fragment or a POST/redirect action endpoint. Names map to functions that
# build the URL path from the deterministic demo seed data.
FULL_PAGE_VIEWS = [
    "about",
    "whats_new",
    "playlists",
    "playlist_info",
    "content_info",
    "player",
    "create_from_resource",
]

# `index` is not listed: it is a redirect to `playlists`, which is audited on its own.
STATIC_PATHS = {
    "about": "/about/",
    "whats_new": "/whats-new/",
    "playlists": "/playlists/",
}


def _resolve_path(view_name: str) -> str:
    if view_name in STATIC_PATHS:
        return STATIC_PATHS[view_name]

    # Owned by the demo admin (the account dev-quick-login authenticates as), so
    # every view-permission check passes.
    playlist = Playlist.objects.get(name="Local Admin / Demo Review Shelf")
    content = Content.objects.get(title="Birds Overview")

    return {
        "playlist_info": f"/playlists/{playlist.id}/",
        "content_info": f"/content/{content.id}/display-settings/",
        "player": f"/player/{content.id}/",
        "create_from_resource": f"/playlists/{playlist.id}/create-from-resource/",
    }[view_name]


def _run_axe(page: Page) -> dict:
    page.add_script_tag(content=AXE_MIN_JS.read_text(encoding="utf-8"))
    return page.evaluate("async () => await axe.run()")


def _format_violations(violations: list[dict]) -> str:
    lines = [f"{len(violations)} accessibility violation(s) found:"]
    for v in violations:
        targets = ", ".join(t for node in v["nodes"] for t in node["target"])
        lines.append(f"  [{v['impact']}] {v['id']}: {v['help']} ({v['helpUrl']})")
        lines.append(f"    affected: {targets}")
    return "\n".join(lines)


@pytest.mark.parametrize("view_name", FULL_PAGE_VIEWS)
def test_full_page_view_has_no_a11y_violations(
    view_name: str, logged_in_page: Page, live_server
):
    page = logged_in_page
    response = page.goto(f"{live_server.url}{_resolve_path(view_name)}")
    assert response is not None and response.ok, f"{view_name} did not load"

    results = _run_axe(page)

    violations = results["violations"]
    assert not violations, _format_violations(violations)


def test_the_blur_editing_ui_has_no_a11y_violations(page: Page, open_editor):
    """The blur points panel and the editable video frame, with a blur selected.

    Scoped to those two subtrees rather than added to FULL_PAGE_VIEWS above, because the video
    editor page as a whole carries violations that predate this feature (contrast, unlabelled
    controls, and ~50 nodes outside any landmark). Asserting the whole page would fail for reasons
    no blur change can fix, and would then be disabled - so this asserts what the feature owns and
    keeps failing for real when it regresses.

    Worth having because a blur is placed by dragging, and dragging is the one interaction a
    keyboard user cannot perform: the rig's arrow keys, the focusable timeline dots and the panel's
    numeric fields are the whole of their access to the feature.
    """
    from core.models import BlurAnnotation

    open_editor()

    blur = BlurAnnotation.objects.get(name="Bird Flight Path")
    item = page.locator(
        f'.track-item[data-annotation-type="blur"][data-annotation-id="{blur.pk}"]'
    )
    item.locator(".track-item-content").click()
    page.wait_for_selector("#blur-edit-rig", state="attached")

    # Select a row, because a point's delete button is `display: none` until its row is the active
    # one - and axe skips hidden elements, so without this the buttons are never audited at all.
    page.locator("#blur-positions-wrapper .position-entry").nth(1).click()
    page.wait_for_selector(".active-position-entry .blur-position-delete-button")

    page.add_script_tag(content=AXE_MIN_JS.read_text(encoding="utf-8"))
    results = page.evaluate(
        """async () => await axe.run({
            include: [['#blur-positions-wrapper'], ['#annotation-box']],
        })"""
    )

    assert not results["violations"], _format_violations(results["violations"])


def test_the_youtube_editor_banner_has_no_a11y_violations(
    fake_youtube, live_server, youtube_content, page: Page
):
    """The YouTube warning banner, scoped the same way and for the same reason as the test above.

    The video editor page is not in FULL_PAGE_VIEWS because it carries pre-existing violations, so
    without this the banner - markup that only ever renders for YouTube content - would be audited
    nowhere.
    """
    content = youtube_content("A11y - YouTube Banner")

    page.goto(f"{live_server.url}/login/dev/quick/")
    page.goto(f"{live_server.url}/video-editor/{content.pk}/")
    page.wait_for_selector("#youtube-editor-warning-banner")

    page.add_script_tag(content=AXE_MIN_JS.read_text(encoding="utf-8"))
    results = page.evaluate(
        """async () => await axe.run({
            include: [['#youtube-editor-warning-banner']],
        })"""
    )

    assert not results["violations"], _format_violations(results["violations"])


def test_the_add_video_dialog_has_no_a11y_violations(logged_in_page: Page, live_server):
    """The add-a-video dialog, which the playlist_info sweep above cannot reach.

    axe skips hidden content, and a <dialog> is hidden until it is opened - so the YouTube form
    inside it, labels and error region included, is invisible to the page-level audit.
    """
    playlist = Playlist.objects.get(name="Local Admin / Demo Review Shelf")
    page = logged_in_page
    page.goto(f"{live_server.url}/playlists/{playlist.id}/")
    page.locator("[commandfor='add-video-dialog']").first.click()
    page.wait_for_selector("#add-youtube-video-form", state="visible")

    page.add_script_tag(content=AXE_MIN_JS.read_text(encoding="utf-8"))
    results = page.evaluate("async () => await axe.run({include: [['dialog[open]']]})")

    assert not results["violations"], _format_violations(results["violations"])
