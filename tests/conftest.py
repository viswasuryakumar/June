import os
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
DUMMY_EXTENSION_PATH = REPO_ROOT / "tests" / "fixtures" / "dummy_extension"


@pytest.fixture
def playwright_launch_args() -> list[str]:
    """Chromium launch args required to run headless *and* load an
    extension: extensions require Chromium's "new" headless mode.
    """
    return ["--headless=new"]


@pytest.fixture(scope="session", autouse=True)
def _ensure_playwright_browsers_path():
    # In disk-constrained CI/dev environments this repo's browsers may be
    # installed under a non-default PLAYWRIGHT_BROWSERS_PATH (see README /
    # PROGRESS.md). If the caller already exported it, respect that; do
    # nothing here otherwise (falls back to Playwright's default location).
    yield os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
