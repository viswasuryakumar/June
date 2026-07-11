"""Verifies T1.1 / S1.1.2: launch_persistent_context loads an (unpacked)
extension successfully. Uses a trivial placeholder extension created for
test purposes only (tests/fixtures/dummy_extension/) - NOT the real
JobRight extension, which this environment has no access to.
"""

import re

import pytest
from playwright.sync_api import sync_playwright
from tests.conftest import DUMMY_EXTENSION_PATH

pytestmark = pytest.mark.playwright


def test_persistent_context_loads_dummy_extension(tmp_path):
    ext_path = str(DUMMY_EXTENSION_PATH)
    profile_dir = str(tmp_path / "profile")

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            profile_dir,
            headless=False,
            args=[
                "--headless=new",  # required: extensions need headed or new-headless mode
                f"--disable-extensions-except={ext_path}",
                f"--load-extension={ext_path}",
            ],
        )
        try:
            service_worker = context.wait_for_event("serviceworker", timeout=10_000)
            assert service_worker.url.startswith("chrome-extension://")
            assert service_worker.url.endswith("/background.js")

            match = re.match(r"chrome-extension://([a-p]+)/", service_worker.url)
            assert match is not None
            extension_id = match.group(1)
            assert len(extension_id) == 32
        finally:
            context.close()
