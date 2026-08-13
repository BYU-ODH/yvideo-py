"""axe-core sweeps over every template this app can render.

Three things make a template invisible to an accessibility audit, and each one is why a test
below exists rather than just a list of URLs:

* axe skips anything hidden, so a <dialog> that is never opened, a menu that is never
  unrolled and a panel that is never switched to are all audited as if they did not exist.
  Hence the dialog, menu and panel tests: they open the thing first.
* a template only renders the branches its context data reaches. The annotation form is
  seven different forms depending on the annotation type; the editor draws nothing at all
  without an annotation set; the clips-only warning needs a content that has no clips. Hence
  `content_with_every_annotation_type` and the deliberately awkward contents built alongside it.
* a rule can only fail against the state that is on screen. Hover backgrounds, the spoofing
  banner and a form's error state are each a different set of colours and elements from the
  resting page, so they are visited on purpose.

test_every_template_is_audited_or_explained is the guard that keeps this honest: it fails when
a template exists that no test here renders and no exclusion explains.
"""

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

TEMPLATE_ROOTS = (
    Path(__file__).resolve().parents[2] / "core" / "templates",
    Path(__file__).resolve().parents[2] / "templates",
)

PLAYER_READY = (
    "() => Boolean(window.videoPlayer && "
    "document.querySelector('.annotation-player-container video')?.duration > 0)"
)
# The editor additionally has to have drawn its timeline: an audit that runs before
# renderTickMarksAndLabels would miss every track item on the page.
EDITOR_READY = (
    "() => Boolean(window.videoPlayer && "
    "document.getElementById('tick-marks-container')?.children.length > 0)"
)
# YouTube-backed pages report a duration once the IFrame API is ready, which is also when
# the <iframe> it builds - and the accessible name we give it - exists.
YOUTUBE_READY = (
    "() => { const yt = document.querySelector('youtube-video');"
    " return Boolean(yt && !isNaN(yt.duration) && yt.duration > 0); }"
)

READY_TIMEOUT_MS = 10_000


def _run_axe(page: Page, include=None, exclude=None) -> dict:
    page.add_script_tag(content=AXE_MIN_JS.read_text(encoding="utf-8"))
    options = {}
    if include:
        options["include"] = [[selector] for selector in include]
    if exclude:
        options["exclude"] = [[selector] for selector in exclude]
    return page.evaluate("async (options) => await axe.run(options)", options)


def _format_violations(violations: list[dict], where: str) -> str:
    lines = [f"{len(violations)} accessibility violation(s) found in {where}:"]
    for v in violations:
        targets = ", ".join(t for node in v["nodes"] for t in node["target"])
        lines.append(f"  [{v['impact']}] {v['id']}: {v['help']} ({v['helpUrl']})")
        lines.append(f"    affected: {targets}")
        reasons = {
            check["message"]
            for node in v["nodes"]
            for check in node["any"] + node["all"] + node["none"]
        }
        for reason in sorted(reasons)[:3]:
            lines.append(f"    why: {reason}")
    return "\n".join(lines)


def assert_no_violations(page: Page, where: str, include=None, exclude=None) -> None:
    results = _run_axe(page, include=include, exclude=exclude)
    assert not results["violations"], _format_violations(results["violations"], where)


# ---------------------------------------------------------------------------- fixtures


@pytest.fixture
def content_with_every_annotation_type(seeded_demo_data):
    """One content carrying every annotation type, spread over three tracks.

    The seed covers the five types the demo needs to look real; this covers the two it does
    not (skip and pause) and both remaining blank types, because the editor's detail form,
    its panel groups and its timeline items are all per-type templates. Three tracks because
    a single track hides every "is this the first/last track" branch in the track menu.
    """
    from core.factories import AnnotationSetFactory
    from core.factories import BlankAnnotationFactory
    from core.factories import BlurAnnotationFactory
    from core.factories import BlurAnnotationPositionFactory
    from core.factories import ClipFactory
    from core.factories import CommentAnnotationFactory
    from core.factories import ContentFactory
    from core.factories import MuteAnnotationFactory
    from core.factories import TrackFactory
    from core.models import PauseAnnotation
    from core.models import SkipAnnotation

    playlist = Playlist.objects.get(name="Demo Review Shelf")
    birds = Content.objects.get(title="Birds Overview")

    annotation_set = AnnotationSetFactory(
        name="A11y Every Annotation Type",
        resource=birds.resource_file.resource,
        owner=playlist.owner,
    )
    first = TrackFactory(
        annotation_set=annotation_set, name="Track 1", stack_position=0
    )
    second = TrackFactory(
        annotation_set=annotation_set, name="Track 2", stack_position=1
    )
    third = TrackFactory(
        annotation_set=annotation_set, name="Track 3", stack_position=2
    )

    ClipFactory(track=first, name="A11y Clip", start_time=1.0, end_time=8.0)
    MuteAnnotationFactory(track=first, name="A11y Mute", start_time=2.0, end_time=4.0)
    SkipAnnotation.objects.create(
        track=first,
        name="A11y Skip",
        start_time=9.0,
        end_time=10.0,
        message="Skipping a section.",
    )
    # All three blank types, because each is a different <option selected> in the form.
    for name, blank_type, start in (
        ("A11y Blank Black", "k", 1.0),
        ("A11y Blank Blur", "#", 3.0),
        ("A11y Blank White", "w", 5.0),
    ):
        BlankAnnotationFactory(
            track=second,
            name=name,
            start_time=start,
            end_time=start + 1.0,
            type=blank_type,
        )
    PauseAnnotation.objects.create(
        track=second,
        name="A11y Pause",
        start_time=6.5,
        end_time=6.5,
        message="Pause and discuss.",
    )
    CommentAnnotationFactory(
        track=third,
        name="A11y Comment",
        start_time=1.0,
        end_time=9.0,
        text="A comment that is drawn over the video.",
    )
    blur = BlurAnnotationFactory(
        track=third, name="A11y Blur", start_time=2.0, end_time=7.0
    )
    for time, x, y, width, height in (
        (2.0, 10.0, 20.0, 15.0, 12.0),
        (7.0, 50.0, 40.0, 20.0, 9.0),
    ):
        BlurAnnotationPositionFactory(
            blur_annotation=blur, time=time, x=x, y=y, width=width, height=height
        )

    return ContentFactory(
        playlist=playlist,
        resource_file=birds.resource_file,
        annotation_set=annotation_set,
        title="A11y Everything",
        description="Carries one of every annotation type.",
        published=True,
    )


@pytest.fixture
def awkward_data(seeded_demo_data, settings):
    """The states the demo seed has no reason to contain, keyed for the page sweep.

    Empty and missing things are their own templates - "no videos here", the editor's
    no-annotation-set path, the clips-only warning that only paints when a content has no
    clips - and none of them render on the seeded happy path.
    """
    from types import SimpleNamespace

    from core.factories import ContentFactory
    from core.factories import PlaylistFactory
    from core.legacy_migration import LegacyMigrationRequest
    from core.models import ResourceIntakeRequest

    settings.LEGACY_MIGRATION_ENABLED = True

    playlist = Playlist.objects.get(name="Demo Review Shelf")
    birds = Content.objects.get(title="Birds Overview")

    return SimpleNamespace(
        playlist=playlist,
        birds=birds,
        empty_playlist=PlaylistFactory(
            owner=playlist.owner, name="Empty Shelf", published=False
        ),
        content_without_annotation_set=ContentFactory(
            playlist=playlist,
            resource_file=birds.resource_file,
            title="A11y No Annotation Set",
            published=True,
        ),
        clips_only_content_without_clips=ContentFactory(
            playlist=playlist,
            resource_file=birds.resource_file,
            title="A11y Clips Only Without Clips",
            published=True,
            clips_only=True,
        ),
        resource_intake_request=ResourceIntakeRequest.objects.create(
            owner=playlist.owner,
            resource_title="A11y Intake Request",
            audio_language="English",
            acknowledged_compliance=True,
            acknowledged_fair_use_limitation=True,
        ),
        migration_request=LegacyMigrationRequest.objects.create(
            requested_by=playlist.owner,
            target_owner=playlist.owner,
            legacy_reference="Legacy Collection 1",
            latest_job_error="Something went wrong during preflight.",
        ),
    )


@pytest.fixture
def read_only_annotation_set_content(seeded_demo_data):
    """A content whose owner may edit the video but not the annotation set it points at.

    Sharing a set rather than copying it is the point of the annotation-set model, so
    "someone else owns these annotations" is a first-class state of the editor: the set
    picker renders disabled, and every other control is intercepted by an offer to copy.
    """
    from core.factories import ContentFactory
    from core.models import AnnotationSet

    ada_playlist = Playlist.objects.get(name="Birds of a Feather")
    bens_set = AnnotationSet.objects.get(name="Professor Ben Grid Annotations")
    grid_content = Content.objects.get(title="Pattern Analysis Warmup")

    return ContentFactory(
        playlist=ada_playlist,
        resource_file=grid_content.resource_file,
        annotation_set=bens_set,
        title="A11y Borrowed Annotations",
        published=True,
    )


# ------------------------------------------------------------------------- full pages

# Every view that renders a complete document, plus the data shapes that change what it
# draws. Anything not here is an HTMX fragment or a POST/redirect endpoint, and is audited
# through the page that swaps it in. `index` is a redirect to `playlists`.
FULL_PAGE_VIEWS = {
    "about": lambda d, everything: "/about/",
    "whats_new": lambda d, everything: "/whats-new/",
    "playlists": lambda d, everything: "/playlists/",
    "playlist_info": lambda d, everything: f"/playlists/{d.playlist.id}/",
    "playlist_info_with_no_videos": lambda d, everything: (
        f"/playlists/{d.empty_playlist.id}/"
    ),
    "content_info": lambda d, everything: f"/content/{d.birds.id}/display-settings/",
    "content_info_warning_about_clips_only": lambda d, everything: (
        f"/content/{d.clips_only_content_without_clips.id}/display-settings/"
    ),
    "player": lambda d, everything: f"/player/{d.birds.id}/",
    "player_with_every_annotation_type": lambda d, everything: (
        f"/player/{everything.pk}/"
    ),
    "player_showing_nothing_because_clips_only": lambda d, everything: (
        f"/player/{d.clips_only_content_without_clips.id}/"
    ),
    "video_editor": lambda d, everything: f"/video-editor/{everything.pk}/",
    "video_editor_without_an_annotation_set": lambda d, everything: (
        f"/video-editor/{d.content_without_annotation_set.pk}/"
    ),
    "invalid_login": lambda d, everything: "/invalid-login",
    "request_resource": lambda d, everything: "/resource-intake-request/",
    "legacy_migration_requests": lambda d, everything: "/legacy-migrations/",
    "legacy_migration_request_detail": lambda d, everything: (
        f"/legacy-migrations/{d.migration_request.pk}/"
    ),
}

# Pages whose content is drawn by JS after load: auditing them any earlier audits an
# empty <video> and a timeline with no items on it.
READY_CONDITIONS = {
    "player": PLAYER_READY,
    "player_with_every_annotation_type": PLAYER_READY,
    "video_editor": EDITOR_READY,
}


@pytest.mark.parametrize("view_name", sorted(FULL_PAGE_VIEWS))
def test_full_page_view_has_no_a11y_violations(
    view_name: str,
    logged_in_page: Page,
    live_server,
    awkward_data,
    content_with_every_annotation_type,
):
    page = logged_in_page
    path = FULL_PAGE_VIEWS[view_name](awkward_data, content_with_every_annotation_type)

    response = page.goto(f"{live_server.url}{path}")
    assert response is not None and response.ok, f"{view_name} did not load"
    ready = READY_CONDITIONS.get(view_name)
    if ready:
        page.wait_for_function(ready, timeout=READY_TIMEOUT_MS)

    assert_no_violations(page, view_name)


def test_the_youtube_editor_has_no_a11y_violations(
    fake_youtube, live_server, youtube_content, page: Page
):
    """The editor as YouTube-backed content draws it: warning banner, no subtitle editor.

    A different page from the file-backed editor above rather than a variation of it - the
    banner renders nowhere else, and the subtitle panel and its switch button are absent.
    """
    content = youtube_content("A11y - YouTube Editor")

    page.goto(f"{live_server.url}/login/dev/quick/")
    page.goto(f"{live_server.url}/video-editor/{content.pk}/")
    page.wait_for_selector("#youtube-editor-warning-banner")
    # The <iframe> is the API's, not ours, and only exists once the player is ready - audit
    # any earlier and the frame that needs an accessible name is not on the page yet.
    page.wait_for_function(YOUTUBE_READY, timeout=READY_TIMEOUT_MS)

    assert_no_violations(page, "youtube video editor")


def test_the_youtube_player_has_no_a11y_violations(
    fake_youtube, live_server, youtube_content, page: Page
):
    """The player with a <youtube-video> element in place of the <video>."""
    content = youtube_content("A11y - YouTube Player")

    page.goto(f"{live_server.url}/login/dev/quick/")
    page.goto(f"{live_server.url}/player/{content.pk}/")
    page.wait_for_function(YOUTUBE_READY, timeout=READY_TIMEOUT_MS)

    assert_no_violations(page, "youtube player")


# ------------------------------------------------------------------------ page states


def test_the_playlists_page_states_have_no_a11y_violations(
    logged_in_page: Page, live_server, awkward_data
):
    """The banner, the sidebar and the add-playlist dialog, none of which start on screen.

    The what's-new banner ships `hidden` and is unhidden only for a viewer who has not
    dismissed it, so clearing localStorage is what makes it auditable at all.
    """
    page = logged_in_page
    page.goto(f"{live_server.url}/playlists/")
    page.evaluate("() => localStorage.clear()")
    page.reload()

    page.wait_for_selector("#whats-new-banner:not([hidden])")
    assert_no_violations(page, "playlists with the what's-new banner")

    page.locator(".sidebar-toggle").click()
    page.wait_for_selector(".sidebar.show")
    assert_no_violations(page, "playlists with the sidebar open")
    page.locator("#sidebar-close").click()

    page.locator("[commandfor='add-new-playlist-dialog'][command='show-modal']").click()
    page.wait_for_selector("#new-playlist-form", state="visible")
    assert_no_violations(page, "the add-playlist dialog", include=["dialog[open]"])


def test_the_playlist_page_dialogs_have_no_a11y_violations(
    logged_in_page: Page, live_server, awkward_data
):
    """The delete confirmation and the course-assignment editor."""
    page = logged_in_page
    page.goto(f"{live_server.url}/playlists/{awkward_data.playlist.id}/")

    page.locator("#playlist-settings-delete").click()
    page.wait_for_selector("#playlist-confirm-delete", state="visible")
    assert_no_violations(page, "the delete-playlist dialog", include=["dialog[open]"])
    page.keyboard.press("Escape")

    page.locator("#playlist-edit-course-assignment-button").click()
    page.wait_for_selector("#assign-course-button", state="visible")
    assert_no_violations(
        page, "the course-assignments dialog", include=["dialog[open]"]
    )
    page.keyboard.press("Escape")


def test_the_manage_people_dialog_has_no_a11y_violations(
    logged_in_page: Page, live_server, awkward_data
):
    """Manage People, its search results, and the remove confirmation.

    The results list is fetched, so the empty <select> the page ships with is not what a
    screen reader meets in practice - the audit has to run against a populated one.

    Ada's shelf rather than awkward_data.playlist because it is the seeded playlist with
    members other than its owner, and the remove confirmation needs a row to open on.
    The demo admin is a superuser, so every row is theirs to manage.
    """
    page = logged_in_page
    populated_playlist = Playlist.objects.get(name="Birds of a Feather")
    page.goto(f"{live_server.url}/playlists/{populated_playlist.id}/")

    page.locator("#playlist-manage-people-button").click()
    page.wait_for_selector("#playlist-member-add-button", state="visible")
    assert_no_violations(page, "the manage-people dialog", include=["dialog[open]"])

    page.locator("#playlist-member-search").fill("a")
    page.wait_for_selector("#playlist-member-results option", state="attached")
    assert_no_violations(
        page, "the manage-people search results", include=["dialog[open]"]
    )

    page.locator(".playlist-member-remove-button").first.click()
    page.wait_for_selector("#playlist-member-confirm-remove", state="visible")
    assert_no_violations(
        page, "the remove-member confirmation", include=["dialog[open]"]
    )


def test_the_add_video_modal_chain_has_no_a11y_violations(
    logged_in_page: Page, live_server, awkward_data
):
    """The add-a-video dialogs, which the playlist_info sweep above cannot reach.

    axe skips hidden content, and a <dialog> is hidden until it is opened - so the YouTube
    form and resource forms inside them are invisible to the page-level audit.
    """
    page = logged_in_page
    page.goto(f"{live_server.url}/playlists/{awkward_data.playlist.id}/")

    page.locator("[commandfor='add-video-dialog'][command='show-modal']").click()
    page.wait_for_selector("#add-youtube-video-form", state="visible")
    assert_no_violations(page, "the add-video dialog", include=["dialog[open]"])

    page.locator("#add-from-resource-button").click()
    page.wait_for_selector("#select-resource-dialog", state="visible")
    assert_no_violations(page, "the resource picker", include=["dialog[open]"])

    page.locator("#modal-resource-list .resource-details").first.click()
    page.wait_for_selector("#create-from-resource-form-submit", state="visible")
    assert_no_violations(page, "the new-video details form", include=["dialog[open]"])


def test_the_content_delete_dialog_has_no_a11y_violations(
    logged_in_page: Page, live_server, awkward_data
):
    page = logged_in_page
    page.goto(f"{live_server.url}/content/{awkward_data.birds.id}/display-settings/")

    page.locator("#content-details-delete-button").click()
    page.wait_for_selector("#content-confirm-delete", state="visible")
    assert_no_violations(page, "the delete-content dialog", include=["dialog[open]"])


def test_the_resource_request_form_has_no_a11y_violations_when_it_rejects_a_submission(
    logged_in_page: Page, live_server, awkward_data
):
    """The invalid state, where fields grow an error class and messages appear.

    Error text and the styles that mark a field invalid exist nowhere on the resting page,
    and a field marked invalid only by colour is exactly the kind of thing axe catches.
    """
    page = logged_in_page
    page.goto(f"{live_server.url}/resource-intake-request/")

    page.locator("#resource-intake-form button[type=submit]").click()
    page.wait_for_selector("#resource-intake-form .resource-intake-field-error")

    assert_no_violations(page, "the rejected resource request form")


def test_the_legacy_migration_form_has_no_a11y_violations_when_it_rejects_a_submission(
    logged_in_page: Page, live_server, awkward_data
):
    """Same reason as the resource request above: the error state is its own rendering."""
    page = logged_in_page
    page.goto(f"{live_server.url}/legacy-migrations/")

    page.locator("#id_acknowledged_compliance").check()
    page.locator("#id_acknowledged_fair_use_limitation").check()
    # The browser's own required-field check would refuse to send this, and what is being
    # audited is the server's rejection - the same response a request that never ran the
    # client-side check gets back.
    page.evaluate(
        "() => { document.querySelector('.legacy-migrations-form').noValidate = true; }"
    )
    page.get_by_role("button", name="Submit request").click()
    page.wait_for_selector(".errorlist")

    assert_no_violations(page, "the rejected legacy migration form")


# ---------------------------------------------------------------------- player states


def test_the_player_controls_and_menus_have_no_a11y_violations(
    logged_in_page: Page, live_server, content_with_every_annotation_type
):
    """The control bar's three pop-up menus and the subtitle sidebar.

    All four are built by AnnotationPlayer.js and kept `display: none` until opened, so a
    plain page audit sees the buttons and none of what they open.
    """
    page = logged_in_page
    page.goto(f"{live_server.url}/player/{content_with_every_annotation_type.pk}/")
    page.wait_for_function(PLAYER_READY, timeout=READY_TIMEOUT_MS)

    page.locator(".speed-btn").click()
    assert_no_violations(page, "the playback speed menu")
    page.keyboard.press("Escape")

    page.locator(".captions-btn").click()
    assert_no_violations(page, "the captions menu")

    page.locator(".clips-btn").click()
    assert_no_violations(page, "the clips menu")

    # The sidebar button stays disabled until a caption track is on, so turning captions on
    # is what makes the sidebar reachable - and it audits the captions-on state besides.
    page.locator(".captions-btn").click()
    page.locator('.caption-option[data-subtitle-track="0"]').click()
    page.wait_for_selector(".subtitle-sidebar-btn:not([disabled])")
    assert_no_violations(page, "the player with captions turned on")

    page.locator(".subtitle-sidebar-btn").click()
    assert_no_violations(page, "the subtitle sidebar")


def test_the_player_message_overlays_have_no_a11y_violations(
    logged_in_page: Page, live_server, awkward_data, content_with_every_annotation_type
):
    """The text the player draws over the video when it stops for something.

    A pause annotation's message and the clips-only "nothing to show" notice are both
    written into the annotation box over the picture, so they are text on top of a video
    frame - the one place in this app where what is behind the text is not a colour we
    chose. Neither exists until playback reaches the thing that raises it.
    """
    page = logged_in_page

    page.goto(f"{live_server.url}/player/{content_with_every_annotation_type.pk}/")
    page.wait_for_function(PLAYER_READY, timeout=READY_TIMEOUT_MS)
    page.evaluate("() => { window.videoPlayer.videoElem.currentTime = 6.4; }")
    page.evaluate("() => window.videoPlayer.videoElem.play()")
    page.wait_for_function(
        "() => window.videoPlayer.messageIsShowing", timeout=READY_TIMEOUT_MS
    )
    assert_no_violations(page, "the player showing a pause annotation's message")

    page.goto(
        f"{live_server.url}/player/{awkward_data.clips_only_content_without_clips.id}/"
    )
    page.wait_for_function(PLAYER_READY, timeout=READY_TIMEOUT_MS)
    page.evaluate("() => window.videoPlayer.videoElem.play()")
    page.wait_for_function(
        "() => window.videoPlayer.messageIsShowing", timeout=READY_TIMEOUT_MS
    )
    assert_no_violations(page, "the player refusing to show a clips-only video")


def test_the_unplayable_video_notices_have_no_a11y_violations(
    fake_youtube, live_server, youtube_content, page: Page
):
    """The two failure notices for a video that will not play.

    Both are built in JS and appear only after the player reports an error, so no page-level
    sweep can reach them: the player swaps the frame for a link out, and the editor prepends
    a banner explaining why it never started.
    """
    from tests.e2e.fake_youtube import ERROR_VIDEO_ID

    player_content = youtube_content("A11y - Unplayable Player", ERROR_VIDEO_ID)
    editor_content = youtube_content("A11y - Unplayable Editor", ERROR_VIDEO_ID)

    page.goto(f"{live_server.url}/login/dev/quick/")

    page.goto(f"{live_server.url}/player/{player_content.pk}/")
    page.wait_for_selector(".youtube-video-error")
    assert_no_violations(page, "the player's unplayable video notice")

    page.goto(f"{live_server.url}/video-editor/{editor_content.pk}/")
    page.wait_for_selector("#editor-video-error-banner")
    assert_no_violations(page, "the editor's unplayable video banner")


def test_a_read_only_annotation_set_has_no_a11y_violations(
    logged_in_page: Page, live_server, read_only_annotation_set_content
):
    """The editor for someone who may edit the video but not the annotations on it.

    The set picker renders `disabled` here, and every control in the editor is intercepted
    by the offer to make your own copy - a dialog built in JS that exists in no template and
    appears on no other page.
    """
    page = logged_in_page
    page.goto(f"{live_server.url}/playlists/")

    # Spoofing is the only way to be a non-superuser here: an admin can edit any set, so
    # the read-only branch never renders for the account the other tests use.
    page.locator("#spoof-user-input").fill("Ada")
    page.wait_for_selector("#spoof-user-select option")
    value = page.locator("#spoof-user-select option").first.get_attribute("value")
    page.select_option("#spoof-user-select", value)
    page.locator("#spoof-user-submit").click()
    page.wait_for_selector("#spoof-warning")

    page.goto(f"{live_server.url}/video-editor/{read_only_annotation_set_content.pk}/")
    page.wait_for_function(EDITOR_READY, timeout=READY_TIMEOUT_MS)
    assert_no_violations(page, "the editor with a read-only annotation set")

    page.locator("#timeline-new-track-button").click()
    page.wait_for_selector("#annotation-set-copy-prompt", state="visible")
    assert_no_violations(
        page, "the offer to copy someone else's set", include=["dialog[open]"]
    )


def test_the_player_has_no_a11y_violations_while_annotations_are_on_screen(
    logged_in_page: Page, live_server, content_with_every_annotation_type
):
    """A comment box and a blur overlay drawn over the video.

    Both are appended to the annotation box while their annotation is active and removed
    when it is not, so they only exist at the right playhead position.
    """
    page = logged_in_page
    page.goto(f"{live_server.url}/player/{content_with_every_annotation_type.pk}/")
    page.wait_for_function(PLAYER_READY, timeout=READY_TIMEOUT_MS)

    page.evaluate("() => { window.videoPlayer.videoElem.currentTime = 3; }")
    page.wait_for_selector("#annotation-box [id^=comment-text-box]")
    page.wait_for_selector("#annotation-box .blur-position")

    assert_no_violations(page, "the player with a comment and a blur on screen")


# ---------------------------------------------------------------------- editor states

ANNOTATION_TYPES = ["blank", "blur", "clip", "comment", "mute", "pause", "skip"]


@pytest.mark.parametrize("annotation_type", ANNOTATION_TYPES)
def test_the_annotation_form_has_no_a11y_violations(
    annotation_type: str,
    logged_in_page: Page,
    live_server,
    content_with_every_annotation_type,
):
    """annotation_form.html is seven forms in one template, and each is only reachable by
    selecting an annotation of that type: pause has no end time, comment has the text and
    geometry fields, blank has the type picker, blur carries the whole points table."""
    page = logged_in_page
    page.goto(
        f"{live_server.url}/video-editor/{content_with_every_annotation_type.pk}/"
    )
    page.wait_for_function(EDITOR_READY, timeout=READY_TIMEOUT_MS)

    page.locator(
        f'.track-item[data-annotation-type="{annotation_type}"] .track-item-content'
    ).first.click()
    page.wait_for_selector(
        f'#existing-item-form[data-annotation-type="{annotation_type}"]'
    )

    assert_no_violations(page, f"the {annotation_type} form", include=["#detail-form"])


def test_the_blur_editing_ui_has_no_a11y_violations(page: Page, open_editor):
    """The blur points panel and the editable video frame, with a blur selected.

    Worth its own test even though the blur form is audited above, because a blur is placed
    by dragging, and dragging is the one interaction a keyboard user cannot perform: the
    rig's arrow keys, the focusable timeline dots and the panel's numeric fields are the
    whole of their access to the feature.
    """
    from core.models import BlurAnnotation

    open_editor()

    blur = BlurAnnotation.objects.get(name="Bird Flight Path")
    item = page.locator(
        f'.track-item[data-annotation-type="blur"][data-annotation-id="{blur.pk}"]'
    )
    item.locator(".track-item-content").click()
    page.wait_for_selector("#blur-edit-rig", state="attached")

    # Select a row, because a point's delete button is `display: none` until its row is the
    # active one - and axe skips hidden elements, so without this the buttons are never
    # audited at all.
    page.locator("#blur-positions-wrapper .position-entry").nth(1).click()
    page.wait_for_selector(".active-position-entry .blur-position-delete-button")

    assert_no_violations(
        page,
        "the blur editing UI",
        include=["#blur-positions-wrapper", "#annotation-box"],
    )


def test_the_editor_annotation_set_dialogs_have_no_a11y_violations(
    logged_in_page: Page, live_server, content_with_every_annotation_type
):
    """The annotation-set menu and every dialog it opens, including all four option views.

    The options modal swaps one of four fragments into the same dialog, so each has to be
    opened in turn; none of them are in the page as delivered.
    """
    page = logged_in_page
    page.goto(
        f"{live_server.url}/video-editor/{content_with_every_annotation_type.pk}/"
    )
    page.wait_for_function(EDITOR_READY, timeout=READY_TIMEOUT_MS)

    page.locator("#annotation-set-menu-button").click()
    page.wait_for_selector(".visible-multi-select-menu")
    assert_no_violations(
        page, "the annotation set menu", include=["#annotation-panel-header"]
    )

    # Clicked through JS rather than Playwright: the menu that holds these buttons overlaps
    # the button that opened it, so a real click lands on the wrong element.
    page.evaluate("() => document.getElementById('annotation-settings-button').click()")
    page.wait_for_selector("#annotation-set-settings-compact", state="visible")
    assert_no_violations(
        page, "the annotation set settings dialog", include=["dialog[open]"]
    )
    page.keyboard.press("Escape")

    page.evaluate(
        "() => document.getElementById('annotation-set-export-open-button').click()"
    )
    page.wait_for_selector("#annotation-set-export-button", state="visible")
    assert_no_violations(page, "the export dialog", include=["dialog[open]"])
    page.keyboard.press("Escape")

    page.evaluate(
        "() => document.getElementById('open-annotation-set-options-modal').click()"
    )
    page.wait_for_selector("#annotation-set-selection-options", state="visible")
    assert_no_violations(
        page, "the annotation set options dialog", include=["dialog[open]"]
    )

    for button_id, description in (
        ("#annotation-set-view-existing", "use an existing set"),
        ("#annotation-set-view-copy", "copy from a set"),
        ("#annotation-set-view-import", "import from a file"),
        ("#annotation-set-view-create", "create a new set"),
    ):
        page.locator(button_id).click()
        page.wait_for_selector(
            "#annotation-set-modal-option-display .annotation-set-modal-option-content",
            state="visible",
        )
        assert_no_violations(page, f"the {description} view", include=["dialog[open]"])
        page.locator("#annotation-set-modal-back").click()


def test_the_editor_timeline_controls_have_no_a11y_violations(
    logged_in_page: Page, live_server, content_with_every_annotation_type
):
    """The new-track dialog, a track's options menu, and its rename field.

    The track menu is `display: none` until opened and the rename field is hidden until the
    menu's Rename is chosen, so both are invisible to the page sweep. Three tracks exist, so
    the menu renders its Move up / Move down / Delete branches too.
    """
    page = logged_in_page
    page.goto(
        f"{live_server.url}/video-editor/{content_with_every_annotation_type.pk}/"
    )
    page.wait_for_function(EDITOR_READY, timeout=READY_TIMEOUT_MS)

    page.locator("#timeline-new-track-button").click()
    page.wait_for_selector("#new-track-form", state="visible")
    assert_no_violations(page, "the new track dialog", include=["dialog[open]"])
    page.keyboard.press("Escape")

    # The middle row of three, so the menu renders every branch at once: the first stack
    # position hides Move up and Delete, and the last hides Move down.
    track_row = page.locator(".track-row").nth(1)
    track_row.locator(".open-multi-select-button").click()
    page.wait_for_selector(".visible-multi-select-menu")
    assert_no_violations(page, "a track's options menu", include=[".track-row"])

    track_row.locator(".track-menu-rename").click()
    page.wait_for_selector(".track-rename-wrapper-visible, .track-rename-input:visible")
    assert_no_violations(page, "a track's rename field", include=[".track-row"])


def test_the_editor_hover_states_have_no_a11y_violations(
    logged_in_page: Page, live_server, content_with_every_annotation_type
):
    """Hovered and selected panel items, and a hovered timeline item.

    Each repaints its own background - the panel item's from the annotation type's colour at
    31% (hover) or 46% (selected) over the dark panel - so the white label on it sits on a
    colour that exists in no other state. A hover or selected state has to meet contrast like
    any other, and both are blends, which is the way to get one wrong without noticing.
    """
    page = logged_in_page
    page.goto(
        f"{live_server.url}/video-editor/{content_with_every_annotation_type.pk}/"
    )
    page.wait_for_function(EDITOR_READY, timeout=READY_TIMEOUT_MS)

    # Every type in turn: the blend differs per colour, so one passing says nothing about
    # the rest.
    for annotation_type in ANNOTATION_TYPES:
        page.locator(f"#{annotation_type}-annotation-type-header").click()
        panel_item = page.locator(
            f"#{annotation_type}-annotation-items-list .panel-item"
        ).first
        panel_item.hover()
        assert_no_violations(
            page,
            f"a hovered {annotation_type} panel item",
            include=["#editor-annotation-panel"],
        )

        panel_item.click()
        page.wait_for_selector(f".{annotation_type}-list-item-wrapper-selected")
        assert_no_violations(
            page,
            f"a selected {annotation_type} panel item",
            include=["#editor-annotation-panel"],
        )

    page.locator('.track-item[data-annotation-type="comment"]').first.hover()
    assert_no_violations(page, "a hovered timeline item", include=["#timeline-wrapper"])


def test_the_subtitle_editor_panel_has_no_a11y_violations(
    logged_in_page: Page, live_server, awkward_data
):
    """The subtitle half of the editor: the panel, its settings dialog, and loaded cues.

    The panel starts hidden behind the annotation panel, and its cue list is empty until a
    track is chosen - so neither the cue rows nor their delete buttons exist before that.
    """
    page = logged_in_page
    page.goto(f"{live_server.url}/video-editor/{awkward_data.birds.pk}/")
    page.wait_for_function(EDITOR_READY, timeout=READY_TIMEOUT_MS)

    page.locator("#annotation-panel-switch").click()
    assert_no_violations(
        page, "the empty subtitle panel", include=["#subtitle-editor-panel"]
    )

    page.locator("#subtitle-panel-header .editor-menu-button").click()
    page.wait_for_selector("#subtitles-settings-wrapper", state="visible")
    assert_no_violations(
        page, "the subtitles settings dialog", include=["dialog[open]"]
    )

    track_value = page.locator(
        "#subtitles-track-selector option.subtitles-track-option"
    ).first.get_attribute("value")
    page.select_option("#subtitles-track-selector", track_value)
    page.wait_for_selector(".editor-subtitle-cue")
    assert_no_violations(
        page, "the subtitle cue editor", include=["#subtitle-editor-panel"]
    )


# -------------------------------------------------------------------------- spoofing


def test_the_spoofing_states_have_no_a11y_violations(
    logged_in_page: Page, live_server, awkward_data
):
    """The user search, its results, and every page drawn while spoofing.

    Two templates render only here - the results <option> list, which arrives over HTMX, and
    the warning banner, which replaces the search form for the whole session. Spoofing a
    student also gives the only view of the assigned-courses accordion, which an
    instructor's own playlists page never shows.
    """
    page = logged_in_page
    page.goto(f"{live_server.url}/playlists/")

    page.locator("#spoof-user-input").fill("Student")
    page.wait_for_selector("#spoof-user-select option")
    assert_no_violations(page, "the spoof user search results", include=[".spoof-form"])

    value = page.locator("#spoof-user-select option").first.get_attribute("value")
    page.select_option("#spoof-user-select", value)
    page.locator("#spoof-user-submit").click()
    page.wait_for_selector("#spoof-warning")
    assert_no_violations(page, "the playlists page while spoofing a student")

    page.locator(".course-header").first.click()
    page.wait_for_selector(".course-playlists-list:not(.accordian-folded)")
    assert_no_violations(page, "the assigned-courses accordion, expanded")

    page.locator(".playlists-list-content a").first.click()
    page.wait_for_selector("#playlist-video-list")
    assert_no_violations(page, "a playlist as a student sees it")


# ----------------------------------------------------------------------- django admin

# Our own admin templates: the branding and spoof bar every admin page inherits from
# base_site.html, the add-user form, and the two change forms we extend.
ADMIN_PAGES = {
    "admin_index": lambda d: "/admin/",
    "admin_add_user": lambda d: "/admin/core/user/add/",
    "admin_resource_intake_request": lambda d: (
        f"/admin/core/resourceintakerequest/{d.resource_intake_request.pk}/change/"
    ),
    "admin_legacy_migration_request": lambda d: (
        f"/admin/core/legacymigrationrequest/{d.migration_request.pk}/change/"
    ),
}

# Django's own admin furniture, which these pages inherit and this project does not write:
# the breadcrumb trail's link styling, the inline formset tables' blank header cells, and
# the split date/time widgets' unlabelled inputs. Auditing them would report Django's bugs
# as ours and could never go green; everything else on these pages is still audited.
DJANGO_ADMIN_CHROME = [
    ".breadcrumbs",
    ".inline-group",
    ".form-row.field-last_login",
    ".form-row.field-date_joined",
]


@pytest.mark.parametrize("page_name", sorted(ADMIN_PAGES))
def test_admin_page_has_no_a11y_violations(
    page_name: str, logged_in_page: Page, live_server, awkward_data
):
    page = logged_in_page
    response = page.goto(f"{live_server.url}{ADMIN_PAGES[page_name](awkward_data)}")
    assert response is not None and response.ok, f"{page_name} did not load"

    assert_no_violations(page, page_name, exclude=DJANGO_ADMIN_CHROME)


# ------------------------------------------------------------------- coverage guard

# Templates no test above renders, with the reason. Anything unreachable is listed as such
# rather than deleted here; auditing it would mean testing a view that cannot be called.
UNAUDITED_TEMPLATES = {
    "core/partials/add_playlist_modal.html": (
        "unreferenced: playlists.html uses partials/modals/add_new_playlist.html instead"
    ),
    "core/partials/content_display.html": "unreferenced by any view or template",
    "core/partials/landing_page_playlist_content.html": (
        "unreferenced by any view or template"
    ),
    "core/partials/no_playlists_text.html": "unreferenced by any view or template",
    "core/partials/vtt_cues.html": (
        "unreferenced: the subtitle editor renders partials/subtitle_cues.html"
    ),
}


def test_every_template_is_audited_or_explained():
    """Fails when a template exists that nothing above renders and nothing here excuses.

    The point of this file is that axe sees every template, and the way that quietly stops
    being true is a new template that no test happens to reach. This does not prove a
    template was rendered - it forces whoever adds one to say which test covers it, or to
    write down why none can.
    """
    on_disk = {
        str(path.relative_to(root)).replace("\\", "/")
        for root in TEMPLATE_ROOTS
        for path in root.rglob("*.html")
    }
    accounted_for = AUDITED_TEMPLATES | set(UNAUDITED_TEMPLATES)

    unaccounted = on_disk - accounted_for
    assert not unaccounted, (
        "these templates are audited by no test in this file and explained by no entry in "
        "UNAUDITED_TEMPLATES:\n  " + "\n  ".join(sorted(unaccounted))
    )

    stale = accounted_for - on_disk
    assert not stale, (
        "these templates no longer exist, so their entry here is stale:\n  "
        + "\n  ".join(sorted(stale))
    )


# Which test above renders each template. Kept beside the tests rather than derived from
# them because there is no way to ask a rendered page which templates it came from.
AUDITED_TEMPLATES = {
    # every page
    "core/base.html",
    "core/noncsrf_base.html",
    "core/partials/breadcrumbs.html",
    "core/partials/header.html",
    "core/partials/spoof_form.html",
    "core/partials/spoof_warning.html",
    "core/partials/spoof_user_options_for_select.html",
    # full page sweep
    "core/about.html",
    "core/content_info.html",
    "core/invalid_login.html",
    "core/legacy_migration_request_detail.html",
    "core/legacy_migration_requests.html",
    "core/player.html",
    "core/playlist_info.html",
    "core/playlists.html",
    "core/video_editor.html",
    "core/whats_new.html",
    "core/partials/content_settings_form.html",
    "core/partials/landing_page_playlist.html",
    "core/partials/player-wrapper.html",
    "core/partials/playlist_settings.html",
    "core/partials/resource_intake_request.html",
    "core/partials/whats_new_banner.html",
    # dialogs
    "core/partials/course_assignment.html",
    "core/partials/create_from_resource_form.html",
    "core/partials/dialog_back_button.html",
    "core/partials/dialog_close_button.html",
    "core/partials/modals/add_new_playlist.html",
    "core/partials/modals/add_video.html",
    "core/partials/modals/content_delete.html",
    "core/partials/modals/create_from_resource.html",
    "core/partials/modals/playlist_course_assignments.html",
    "core/partials/modals/playlist_delete.html",
    "core/partials/modals/playlist_member_remove.html",
    "core/partials/modals/playlist_members.html",
    "core/partials/playlist_member_options_for_select.html",
    "core/partials/playlist_members_list.html",
    "core/partials/playlist_members_roster.html",
    "core/partials/modals/select_resource.html",
    "core/partials/resource_details.html",
    "core/partials/resource_list.html",
    "core/partials/user_view_modal.html",
    # editor
    "core/partials/annotation_form.html",
    "core/partials/annotation_list_item.html",
    "core/partials/annotation_panel.html",
    "core/partials/annotation_set_export_modal.html",
    "core/partials/annotation_set_options/base_modal.html",
    "core/partials/annotation_set_options/copy_from_set.html",
    "core/partials/annotation_set_options/create_new.html",
    "core/partials/annotation_set_options/import_from_file.html",
    "core/partials/annotation_set_options/use_existing_set.html",
    "core/partials/annotation_set_selector.html",
    "core/partials/annotation_set_settings_compact.html",
    "core/partials/blur_positions.html",
    "core/partials/item.html",
    "core/partials/item_form_placeholder.html",
    "core/partials/item_undo_redo_buttons.html",
    "core/partials/subtitle_cues.html",
    "core/partials/subtitle_editor_panel.html",
    "core/partials/subtitle_panel_content.html",
    "core/partials/subtitles_settings.html",
    "core/partials/timeline-track-row.html",
    "core/partials/timeline_base.html",
    # django admin
    "admin/base_site.html",
    "admin/core/legacymigrationrequest/change_form.html",
    "admin/core/resourceintakerequest/change_form.html",
    "admin/core/user/add_form.html",
}
