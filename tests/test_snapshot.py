import pytest
from playwright.sync_api import sync_playwright
from src.observability.snapshot import snapshot

pytestmark = pytest.mark.playwright


@pytest.fixture(scope="module")
def sync_page():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto("data:text/html,<html><body><h1>June test page</h1></body></html>")
        yield page
        browser.close()


def test_snapshot_writes_screenshot_and_html(tmp_path, sync_page):
    result = snapshot(sync_page, "unit-test-label", run_id="run-abc", base_dir=tmp_path)

    from pathlib import Path

    screenshot_path = Path(result.screenshot_path)
    html_path = Path(result.html_path)

    assert screenshot_path.exists()
    assert screenshot_path.stat().st_size > 0
    assert html_path.exists()
    assert "June test page" in html_path.read_text()

    run_dir = tmp_path / "run-abc"
    assert screenshot_path.parent == run_dir
    assert html_path.parent == run_dir


def test_snapshot_label_is_slugified(tmp_path, sync_page):
    result = snapshot(sync_page, "weird label/with:chars", run_id="run-xyz", base_dir=tmp_path)
    from pathlib import Path

    assert Path(result.screenshot_path).exists()
    assert "weird-label-with-chars" in result.screenshot_path
