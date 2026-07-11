"""Tests for src/auth/context.py (spec T2.1): persistent context + extension
loading, is_logged_in() health check, locator_present()'s split semantics
(resolved-but-invisible degrades to False; a missing/malformed registry
key raises SelectorBroken - REV-003), persist_storage_state().

No live jobright.ai access exists in this environment - everything here
runs against the dummy test extension (tests/fixtures/dummy_extension) and
local `data:` URL fixture pages, mirroring
tests/test_persistent_context_extension.py and tests/test_selector_broken.py.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright
from src.auth.context import (
    BrowserContextConfig,
    build_launch_args,
    get_context,
    get_extension_id,
    is_logged_in,
    launch_persistent_context,
    locator_present,
    persist_storage_state,
)
from src.contracts.exceptions import SelectorBroken
from src.observability.selectors import SelectorRegistry
from tests.conftest import DUMMY_EXTENSION_PATH

pytestmark = pytest.mark.playwright


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True, channel="chromium")
        yield b
        b.close()


# -- build_launch_args (pure, no browser needed) ----------------------------


def test_build_launch_args_adds_headless_and_extension_flags():
    config = BrowserContextConfig(extension_path=Path("/tmp/some-ext"), headless=True)
    args = build_launch_args(config)
    assert "--headless=new" in args
    assert "--disable-extensions-except=/tmp/some-ext" in args
    assert "--load-extension=/tmp/some-ext" in args


def test_build_launch_args_headless_false_omits_headless_flag():
    config = BrowserContextConfig(headless=False)
    args = build_launch_args(config)
    assert "--headless=new" not in args


# -- persistent context + dummy extension -----------------------------------


def test_persistent_context_loads_dummy_extension_and_returns_extension_id(tmp_path):
    config = BrowserContextConfig(
        profile_dir=tmp_path / "profile",
        extension_path=DUMMY_EXTENSION_PATH,
        headless=True,
    )
    with sync_playwright() as p:
        context = launch_persistent_context(config, p)
        try:
            extension_id = get_extension_id(context)
            assert extension_id is not None
            assert re.fullmatch(r"[a-p]{32}", extension_id)
        finally:
            context.close()


def test_get_context_manager_yields_usable_context(tmp_path):
    config = BrowserContextConfig(profile_dir=tmp_path / "profile2", headless=True)
    with get_context(config) as context:
        page = context.new_page()
        page.goto("data:text/html,<html><body><h1>hi</h1></body></html>")
        assert "hi" in page.content()


def test_get_extension_id_returns_none_when_no_extension_configured(tmp_path):
    config = BrowserContextConfig(profile_dir=tmp_path / "profile3", headless=True)
    with sync_playwright() as p:
        context = launch_persistent_context(config, p)
        try:
            # No extension_path configured -> no serviceworker will ever
            # register, so this must return None rather than hang/raise.
            assert get_extension_id(context, timeout_ms=500) is None
        finally:
            context.close()


# -- is_logged_in() / locator_present() -------------------------------------


def test_is_logged_in_true_when_dashboard_indicator_present(browser):
    page = browser.new_page()
    page.goto(
        "data:text/html,<html><body><div data-testid='user-avatar'>Avatar</div></body></html>"
    )
    registry = SelectorRegistry({"login": {"dashboard_indicator": "[data-testid=user-avatar]"}})
    assert is_logged_in(page, registry, "run-ctx-1", timeout_ms=500) is True
    page.close()


def test_is_logged_in_false_on_plain_login_form_without_raising(browser):
    page = browser.new_page()
    page.goto(
        "data:text/html,<html><body><form>"
        "<input id='email'><input id='password'>"
        "<button type='submit'>Login</button>"
        "</form></body></html>"
    )
    registry = SelectorRegistry({"login": {"dashboard_indicator": "[data-testid=user-avatar]"}})
    # A logged-out page is a normal state - this must return False, not raise
    # SelectorBroken (that's is_logged_in's whole point per its docstring).
    assert is_logged_in(page, registry, "run-ctx-2", timeout_ms=300) is False
    page.close()


def test_is_logged_in_raises_selector_broken_when_selector_key_missing_entirely(
    tmp_path, monkeypatch, browser
):
    """S2.1.3 edge case: the registry has no 'login' section at all (not
    just "user happens to be logged out") - a structural config break, not
    a normal page state. `locator_present()` catches the `KeyError` from
    `SelectorRegistry.get()` and raises `SelectorBroken` (REV-003) rather
    than degrading to False, so this doesn't get silently confused with a
    logged-out session - verified here against the actual context.py
    implementation rather than assumed.
    """
    monkeypatch.chdir(tmp_path)  # snapshot() writes under ./runs/<run_id>/
    page = browser.new_page()
    page.goto("data:text/html,<html><body>anything</body></html>")
    registry = SelectorRegistry({})
    with pytest.raises(SelectorBroken) as excinfo:
        is_logged_in(page, registry, "run-ctx-3", timeout_ms=300)
    assert excinfo.value.selector_key == "login.dashboard_indicator"
    page.close()


def test_is_logged_in_raises_selector_broken_when_selector_value_is_malformed(
    tmp_path, monkeypatch, browser
):
    """Registry key resolves to a non-string leaf (malformed selector
    config) - `SelectorRegistry.get()` raises `KeyError` for this too, and
    `locator_present()` again raises `SelectorBroken` (REV-003) rather
    than degrading to False.
    """
    monkeypatch.chdir(tmp_path)
    page = browser.new_page()
    page.goto("data:text/html,<html><body>anything</body></html>")
    registry = SelectorRegistry({"login": {"dashboard_indicator": {"nested": "not-a-string"}}})
    with pytest.raises(SelectorBroken) as excinfo:
        is_logged_in(page, registry, "run-ctx-4", timeout_ms=300)
    assert excinfo.value.selector_key == "login.dashboard_indicator"
    page.close()


def test_locator_present_raises_selector_broken_for_unknown_key_directly(
    tmp_path, monkeypatch, browser
):
    monkeypatch.chdir(tmp_path)
    page = browser.new_page()
    page.goto("data:text/html,<html><body>anything</body></html>")
    registry = SelectorRegistry({})
    with pytest.raises(SelectorBroken) as excinfo:
        locator_present(page, registry, "nope.nothere", run_id="run-ctx-5", timeout_ms=200)
    assert excinfo.value.selector_key == "nope.nothere"
    page.close()


# -- persist_storage_state ---------------------------------------------------


def test_persist_storage_state_writes_file(tmp_path, browser):
    context = browser.new_context()
    page = context.new_page()
    page.goto("data:text/html,<html><body>x</body></html>")
    profile_dir = tmp_path / "profile4"
    path = persist_storage_state(context, profile_dir=profile_dir)
    assert path.exists()
    assert path == profile_dir / "storage_state.json"
    context.close()
