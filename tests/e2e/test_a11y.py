from pathlib import Path

from playwright.sync_api import Page
from playwright.sync_api import expect
import pytest

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


def test_index_page_has_no_a11y_violations(page: Page, live_server, seeded_demo_data):
    page.goto(f"{live_server.url}/login/dev/quick/")
    expect(page).to_have_url(f"{live_server.url}/")
    expect(page.locator("#index-page-title h1")).to_be_visible()

    results = _run_axe(page)

    violations = results["violations"]
    assert not violations, _format_violations(violations)
