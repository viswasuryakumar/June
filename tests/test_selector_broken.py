import pytest
from playwright.sync_api import sync_playwright
from src.contracts.exceptions import SelectorBroken
from src.observability.selectors import SelectorRegistry, resolve_locator

pytestmark = pytest.mark.playwright


@pytest.fixture(scope="module")
def sync_page():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, channel="chromium")
        page = browser.new_page()
        page.goto(
            "data:text/html,<html><body><button id='real-button'>Click</button></body></html>"
        )
        yield page
        browser.close()


def test_resolve_existing_selector_succeeds(tmp_path, sync_page, monkeypatch):
    monkeypatch.chdir(tmp_path)
    registry = SelectorRegistry({"widgets": {"button": "#real-button"}})
    locator = resolve_locator(
        sync_page, registry, "widgets.button", run_id="run-1", timeout_ms=2000
    )
    assert locator.inner_text() == "Click"


def test_missing_selector_raises_selector_broken_and_snapshots(tmp_path, sync_page, monkeypatch):
    monkeypatch.chdir(tmp_path)
    registry = SelectorRegistry({"widgets": {"button": "#does-not-exist"}})

    with pytest.raises(SelectorBroken) as excinfo:
        resolve_locator(sync_page, registry, "widgets.button", run_id="run-2", timeout_ms=500)

    err = excinfo.value
    assert err.selector_key == "widgets.button"
    assert err.ticket_kind == "selector_broken"
    assert err.snapshot_path is not None

    from pathlib import Path

    assert Path(err.snapshot_path).exists()


def test_unknown_key_also_raises_selector_broken(tmp_path, sync_page, monkeypatch):
    monkeypatch.chdir(tmp_path)
    registry = SelectorRegistry({})
    with pytest.raises(SelectorBroken):
        resolve_locator(sync_page, registry, "nope.nothere", run_id="run-3", timeout_ms=500)
