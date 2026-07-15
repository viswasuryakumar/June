"""Unit tests for src/executor/extension.py — extension discovery uses
tmp_path fake browser-profile trees; the autofill trigger uses data: URL
fixture pages (never the real extension)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright
from src.executor.extension import (
    JOBRIGHT_EXTENSION_ID,
    find_installed_jobright_extension,
    trigger_extension_autofill,
)


def make_extension(
    root: Path,
    profile: str,
    ext_id: str,
    version: str,
    name: str = "Jobright Autofill",
) -> Path:
    version_dir = root / profile / "Extensions" / ext_id / f"{version}_0"
    version_dir.mkdir(parents=True)
    (version_dir / "manifest.json").write_text(json.dumps({"name": name, "version": version}))
    return version_dir


class TestFindInstalledJobrightExtension:
    def test_found_by_extension_id(self, tmp_path):
        expected = make_extension(tmp_path, "Profile 1", JOBRIGHT_EXTENSION_ID, "1.16.0")
        assert find_installed_jobright_extension([tmp_path]) == expected

    def test_found_by_manifest_name_when_id_differs(self, tmp_path):
        expected = make_extension(tmp_path, "Default", "a" * 32, "2.0.0")
        assert find_installed_jobright_extension([tmp_path]) == expected

    def test_unrelated_extensions_are_ignored(self, tmp_path):
        make_extension(tmp_path, "Default", "b" * 32, "9.9.9", name="Some Other Tool")
        assert find_installed_jobright_extension([tmp_path]) is None

    def test_newest_version_wins(self, tmp_path):
        make_extension(tmp_path, "Profile 1", JOBRIGHT_EXTENSION_ID, "1.15.0")
        newest = make_extension(tmp_path, "Profile 3", JOBRIGHT_EXTENSION_ID, "1.16.0")
        assert find_installed_jobright_extension([tmp_path]) == newest

    def test_missing_roots_return_none(self, tmp_path):
        assert find_installed_jobright_extension([tmp_path / "nope"]) is None


@pytest.mark.playwright
class TestTriggerExtensionAutofill:
    @pytest.fixture(scope="class")
    def page(self):
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, channel="chromium")
            page = browser.new_page()
            yield page
            browser.close()

    def test_no_injected_ui_reports_not_triggered(self, page):
        page.goto("data:text/html,<form><input name='email'></form>")
        attempt = trigger_extension_autofill(page, wait_ms=50)
        assert attempt.triggered is False

    def test_injected_button_is_clicked_and_changes_measured(self, page):
        page.goto(
            "data:text/html,<form><input name='email'></form>"
            "<button id='jobright-autofill-button' onclick=\""
            "document.querySelector('input').value='filled@example.com'\">"
            "Autofill</button>"
        )
        attempt = trigger_extension_autofill(page, wait_ms=50)
        assert attempt.triggered is True
        assert attempt.changed_fields == 1
        # matched by exact-text first (button says "Autofill") or by id
        assert attempt.detail.startswith(("text=", "#jobright-autofill-button"))

    def test_registry_key_takes_precedence(self, page):
        class FakeRegistry:
            def get(self, key):
                assert key == "extension.autofill_trigger"
                return "#custom-trigger"

        page.goto(
            "data:text/html,<input name='n'>"
            "<button id='custom-trigger' onclick=\""
            "document.querySelector('input').value='x'\">Fill</button>"
        )
        attempt = trigger_extension_autofill(page, registry=FakeRegistry(), wait_ms=50)
        assert attempt.triggered and attempt.detail.startswith("#custom-trigger")
