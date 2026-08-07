import re

from playwright.sync_api import expect
import pytest

from core.models import Content

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.django_db(transaction=True),
]


def test_clips_only_checkbox_shows_warning_live_when_no_clips_are_defined(
    logged_in_page, live_server
):
    page = logged_in_page
    # "Birds Draft Discussion" shares the real birds.mp4 file but has no
    # annotation_set at all, so it has zero clips.
    content = Content.objects.get(title="Birds Draft Discussion")

    page.goto(f"{live_server.url}/content/display-settings/{content.pk}/")

    checkbox = page.locator("#clips-only")
    warning = page.locator("#clips-only-warning")
    form_group = page.locator("#clips-only-form-group")

    expect(checkbox).not_to_be_checked()
    expect(warning).to_be_hidden()

    checkbox.check()

    expect(warning).to_be_visible()
    expect(warning).to_contain_text("This video has no clips defined")
    expect(form_group).to_have_class(re.compile(r"clips-only-invalid"))

    checkbox.uncheck()

    expect(warning).to_be_hidden()
    expect(form_group).not_to_have_class(re.compile(r"clips-only-invalid"))
