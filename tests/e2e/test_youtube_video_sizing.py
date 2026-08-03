import pytest

from core.models import Content
from core.models import Playlist
from core.youtube import get_or_create_youtube_resource

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.django_db(transaction=True),
]


def _create_youtube_content(title):
    playlist = Playlist.objects.get(name="Local Admin / Demo Review Shelf")
    resource = get_or_create_youtube_resource("eHEsJyVQn3w", playlist.owner.username)
    return Content.objects.create(
        playlist=playlist,
        title=title,
        url="https://www.youtube.com/watch?v=eHEsJyVQn3w",
        resource=resource,
    )


def _open_editor_and_wait_for_video(page, live_server, content):
    page.goto(f"{live_server.url}/login/dev/quick/")
    page.goto(f"{live_server.url}/video-editor/{content.pk}/")

    page.wait_for_function(
        """() => {
            const yt = document.querySelector('youtube-video');
            return yt && yt.querySelector('iframe') && !isNaN(yt.duration) && yt.duration > 0;
        }""",
        timeout=15000,
    )
    # Let AnnotationPlayer's resize/loadedmetadata-driven sizing settle.
    page.wait_for_timeout(300)


def _rect(page, selector):
    return page.eval_on_selector(selector, "el => el.getBoundingClientRect().toJSON()")


def _approx_equal_rects(a, b, tol=1.0):
    return all(abs(a[k] - b[k]) <= tol for k in ("x", "y", "width", "height"))


def test_youtube_iframe_fills_annotation_box_in_wide_mode(
    page, live_server, seeded_demo_data
):
    content = _create_youtube_content("Sizing - Wide Mode")
    page.set_viewport_size({"width": 1280, "height": 900})
    _open_editor_and_wait_for_video(page, live_server, content)

    wrapper = _rect(page, ".video-wrapper")
    youtube_video = _rect(page, "youtube-video")
    iframe = _rect(page, "youtube-video iframe")
    annotation_box = _rect(page, ".annotation-box")

    assert wrapper["width"] > 0 and wrapper["height"] > 0
    assert _approx_equal_rects(wrapper, youtube_video)
    assert _approx_equal_rects(wrapper, iframe)
    assert _approx_equal_rects(wrapper, annotation_box)


def test_youtube_iframe_fills_annotation_box_in_full_height_mode(
    page, live_server, seeded_demo_data
):
    content = _create_youtube_content("Sizing - Full Height Mode")
    # Ultra-wide, short viewport: the container's aspect ratio exceeds the
    # assumed 16:9 video ratio, flipping AnnotationPlayer into full-height
    # (pillarboxed) mode.
    page.set_viewport_size({"width": 1600, "height": 500})
    _open_editor_and_wait_for_video(page, live_server, content)

    wrapper = _rect(page, ".video-wrapper")
    youtube_video = _rect(page, "youtube-video")
    iframe = _rect(page, "youtube-video iframe")
    annotation_box = _rect(page, ".annotation-box")

    assert page.eval_on_selector(
        ".video-wrapper", "el => el.classList.contains('full-height')"
    )
    assert wrapper["width"] > 0 and wrapper["height"] > 0
    assert _approx_equal_rects(wrapper, youtube_video)
    assert _approx_equal_rects(wrapper, iframe)
    assert _approx_equal_rects(wrapper, annotation_box)
