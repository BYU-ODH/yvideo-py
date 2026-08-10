"""The annotation overlay sits on the picture, not on the element box, for a YouTube embed too.

Run against the fake IFrame Player API. Everything asserted here is our own arithmetic - the
element box the layout gives `<youtube-video>`, the intrinsic ratio it reports, and where
AnnotationPlayer puts the overlay from those two - so a real embed adds nothing but flakiness.
The one thing only a live embed can show, that the API's injected iframe really does fill the
element box, is asserted in tests/e2e/test_youtube_video_controls.py, and what the YouTube player
draws inside that iframe is manual item 7 in MANUAL_TESTING.md.
"""

import pytest

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.django_db(transaction=True),
]

# The intrinsic ratio youtube-video reports for itself - see YouTubeVideoElement's videoWidth for
# why it is a constant rather than the real resolution.
YOUTUBE_ASPECT_RATIO = 16 / 9


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


# Where the picture actually sits inside youtube-video's element box. The box fills whatever space
# the layout gives it, and the YouTube player fits the picture into the iframe the same way
# `object-fit: contain` does for a real <video> - so the leftover space is letterbox (or pillarbox)
# bars, and the element box is not what an annotation's percentages are relative to.
#
# Re-derived from the element's reported videoWidth/videoHeight here rather than by calling the
# app's own contentRect(), so it stays an independent oracle instead of a tautology.
def _picture_rect(page):
    return page.evaluate(
        """() => {
            const el = document.querySelector('youtube-video');
            const box = el.getBoundingClientRect();
            const scale = Math.min(box.width / el.videoWidth, box.height / el.videoHeight);
            const width = el.videoWidth * scale;
            const height = el.videoHeight * scale;
            return {
                x: box.x + (box.width - width) / 2,
                y: box.y + (box.height - height) / 2,
                width: width,
                height: height,
            };
        }"""
    )


def _approx_equal_rects(a, b, tol=1.0):
    return all(abs(a[k] - b[k]) <= tol for k in ("x", "y", "width", "height"))


def _assert_overlay_tracks_picture(page):
    youtube_video = _rect(page, "youtube-video")
    iframe = _rect(page, "youtube-video iframe")
    annotation_box = _rect(page, ".annotation-box")
    picture = _picture_rect(page)

    assert youtube_video["width"] > 0 and youtube_video["height"] > 0
    # The embed fills the custom element's box; the player inside it, not the box, is what
    # letterboxes.
    assert _approx_equal_rects(youtube_video, iframe)
    # So the overlay has to be pinned to the picture rather than to the box, or a blur's
    # percentages land somewhere other than the frame the viewer sees.
    assert _approx_equal_rects(annotation_box, picture)
    assert annotation_box["width"] / annotation_box["height"] == pytest.approx(
        YOUTUBE_ASPECT_RATIO, abs=0.01
    )
    # ...and the picture is inside the box it was measured from, never spilling past it.
    assert annotation_box["width"] <= youtube_video["width"] + 1
    assert annotation_box["height"] <= youtube_video["height"] + 1


def test_youtube_overlay_tracks_picture_in_wide_viewport(
    fake_youtube, live_server, youtube_content, page
):
    page.set_viewport_size({"width": 1280, "height": 900})
    _open_editor_and_wait_for_video(
        page, live_server, youtube_content("Sizing - Wide Viewport")
    )

    _assert_overlay_tracks_picture(page)


def test_youtube_overlay_tracks_picture_in_short_viewport(
    fake_youtube, live_server, youtube_content, page
):
    # Ultra-wide and short, so the element box ends up a different shape than in the test above
    # and the bars fall on the other axis.
    page.set_viewport_size({"width": 1600, "height": 500})
    _open_editor_and_wait_for_video(
        page, live_server, youtube_content("Sizing - Short Viewport")
    )

    _assert_overlay_tracks_picture(page)
