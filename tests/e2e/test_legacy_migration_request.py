from playwright.sync_api import expect
import pytest

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.django_db(transaction=True),
]


def test_submit_is_enabled_only_after_both_legal_acknowledgements(
    logged_in_page, live_server, settings
):
    settings.LEGACY_MIGRATION_ENABLED = True
    page = logged_in_page
    page.goto(f"{live_server.url}/legacy-migrations/")

    submission_group = page.locator(".legacy-migrations-submission")
    compliance = submission_group.locator("#id_acknowledged_compliance")
    fair_use = submission_group.locator("#id_acknowledged_fair_use_limitation")
    submit = submission_group.get_by_role("button", name="Submit request")

    expect(submission_group).to_be_visible()
    expect(submit).to_be_disabled()

    compliance.check()
    expect(submit).to_be_disabled()

    fair_use.check()
    expect(submit).to_be_enabled()

    compliance.uncheck()
    expect(submit).to_be_disabled()
