"""Artifact capture helper (spec T1.4 / S1.4.2).

snapshot(page, label) -> screenshot + HTML dump into runs/<run_id>/.

Supports both the Playwright sync API and async API transparently: if the
given page's methods are coroutine functions, an awaitable snapshot
coroutine is returned instead of a result (call sites in async code should
`await snapshot(...)`; sync call sites get a plain result back).
"""

from __future__ import annotations

import inspect
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

RUNS_DIR = Path("runs")


@dataclass(frozen=True)
class SnapshotResult:
    label: str
    screenshot_path: str
    html_path: str


def _slugify(label: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9._-]+", "-", label).strip("-")
    return slug or "snapshot"


def _run_dir(run_id: str, base_dir: Path | str = RUNS_DIR) -> Path:
    d = Path(base_dir) / run_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def _paths_for(run_id: str, label: str, base_dir: Path | str = RUNS_DIR) -> tuple[Path, Path]:
    d = _run_dir(run_id, base_dir)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%f")
    stem = f"{stamp}_{_slugify(label)}"
    return d / f"{stem}.png", d / f"{stem}.html"


def snapshot(page, label: str, run_id: str, base_dir: Path | str = RUNS_DIR):
    """Capture a screenshot + HTML dump of `page` under runs/<run_id>/.

    Works with a Playwright sync `Page`. For an async `Page`, use
    `snapshot_async` (or await the coroutine returned when an async page's
    methods are detected).
    """
    if inspect.iscoroutinefunction(getattr(page, "screenshot", None)):
        return snapshot_async(page, label, run_id, base_dir)

    screenshot_path, html_path = _paths_for(run_id, label, base_dir)
    page.screenshot(path=str(screenshot_path))
    html_path.write_text(page.content())
    return SnapshotResult(
        label=label, screenshot_path=str(screenshot_path), html_path=str(html_path)
    )


async def snapshot_async(
    page, label: str, run_id: str, base_dir: Path | str = RUNS_DIR
) -> SnapshotResult:
    """Async-API counterpart of `snapshot`."""
    screenshot_path, html_path = _paths_for(run_id, label, base_dir)
    await page.screenshot(path=str(screenshot_path))
    html_path.write_text(await page.content())
    return SnapshotResult(
        label=label, screenshot_path=str(screenshot_path), html_path=str(html_path)
    )
