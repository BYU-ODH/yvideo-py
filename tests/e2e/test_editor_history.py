import re

from playwright.sync_api import expect
import pytest

from core.models import Content
from core.models import MuteAnnotation

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.django_db(transaction=True),
]


def _open_editor_with_annotation(page, live_server):
    content = Content.objects.get(title="Birds Overview")
    annotation = MuteAnnotation.objects.filter(
        track__annotation_set=content.annotation_set,
        active=True,
    ).first()

    page.goto(f"{live_server.url}/login/dev/quick/")
    page.goto(f"{live_server.url}/video-editor/{content.pk}/")
    page.wait_for_function(
        """() => {
            const video = document.querySelector('.annotation-player-container video');
            return window.videoPlayer && video && !isNaN(video.duration) && video.duration > 0;
        }""",
        timeout=5000,
    )
    page.locator(f"#mute-{annotation.id} .track-item-content").click()
    expect(page.locator("#existing-item-form")).to_have_attribute(
        "data-annotation-id", str(annotation.id)
    )
    return annotation


def _active_form_id(page):
    return page.locator("#existing-item-form").get_attribute("data-annotation-id")


def test_edit_undo_redo_and_branching_stay_in_sync(
    page, live_server, seeded_demo_data
):
    annotation = _open_editor_with_annotation(page, live_server)
    original_id = str(annotation.id)
    original_name = annotation.name

    toolbar = page.locator(".item-form-header .undo-redo-toolbar")
    expect(toolbar).to_be_visible()
    expect(toolbar.locator(".undo-btn img")).to_have_attribute(
        "src", re.compile(r"undo(?:\.[a-z0-9]+)?\.svg$")
    )
    expect(toolbar.locator(".redo-btn img")).to_have_attribute(
        "src", re.compile(r"redo(?:\.[a-z0-9]+)?\.svg$")
    )
    assert toolbar.bounding_box()["y"] < page.locator(".form-group").first.bounding_box()["y"]
    expect(toolbar.locator(".undo-btn")).to_be_disabled()
    expect(toolbar.locator(".redo-btn")).to_be_disabled()

    page.locator("#annotation_name").fill("History edit")
    page.locator("#annotation-form-save-button").click()
    page.wait_for_function(
        f"() => document.querySelector('#existing-item-form')?.dataset.annotationId !== '{original_id}'"
    )
    edited_id = _active_form_id(page)

    expect(page.locator(f"#mute-{original_id}")).to_have_count(0)
    expect(page.locator(f"#mute-{edited_id}")).to_be_visible()
    expect(page.locator(f"#mute-panel-item-{edited_id}")).to_contain_text(
        "History edit"
    )
    expect(page.locator(".undo-btn")).to_be_enabled()
    expect(page.locator(".redo-btn")).to_be_disabled()

    page.locator(".undo-btn").click()
    expect(page.locator("#existing-item-form")).to_have_attribute(
        "data-annotation-id", original_id
    )
    expect(page.locator("#annotation_name")).to_have_value(original_name)
    expect(page.locator(f"#mute-{original_id}")).to_be_visible()
    expect(page.locator(".redo-btn")).to_be_enabled()

    page.locator(".redo-btn").click()
    expect(page.locator("#existing-item-form")).to_have_attribute(
        "data-annotation-id", edited_id
    )
    expect(page.locator("#annotation_name")).to_have_value("History edit")

    # Editing after an undo creates a new linear branch and clears redo.
    page.locator(".undo-btn").click()
    expect(page.locator("#existing-item-form")).to_have_attribute(
        "data-annotation-id", original_id
    )
    page.locator("#annotation_name").fill("Replacement edit")
    page.locator("#annotation-form-save-button").click()
    page.wait_for_function(
        f"() => document.querySelector('#existing-item-form')?.dataset.annotationId !== '{original_id}'"
    )
    replacement_id = _active_form_id(page)

    assert replacement_id != edited_id
    expect(page.locator("#annotation_name")).to_have_value("Replacement edit")
    expect(page.locator(".redo-btn")).to_be_disabled()

