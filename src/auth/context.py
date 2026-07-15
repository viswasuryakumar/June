"""Persistent browser context + login health check (spec T2.1).

`launch_persistent_context` (Epic 1's dummy-extension test already proves
the mechanics: tests/test_persistent_context_extension.py) is reused here
to load the real JobRight extension once its unpacked path is available.
No live JobRight access exists in this environment - everything here is
built/testable against local fixtures (dummy extension, `data:` URL pages).
"""

from __future__ import annotations

import re
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from src.contracts.exceptions import SelectorBroken
from src.observability.logging import log_event
from src.observability.selectors import SelectorRegistry
from src.observability.snapshot import snapshot

DEFAULT_PROFILE_DIR = Path.home() / ".jobright-autopilot" / "profile"
DEFAULT_STORAGE_STATE_FILENAME = "storage_state.json"

# Pinned, stable values (S2.1.1) so the browser fingerprint doesn't drift
# run to run - one of the mitigations spec §6 calls out for the "account
# flagged for bot-like behavior" risk.
DEFAULT_VIEWPORT = {"width": 1440, "height": 900}
DEFAULT_TIMEZONE_ID = "America/Los_Angeles"
DEFAULT_LOCALE = "en-US"

_EXTENSION_ID_RE = re.compile(r"chrome-extension://([a-p]+)/")


@dataclass(frozen=True)
class BrowserContextConfig:
    """Everything needed to launch/load the persistent Chromium profile.

    Injectable end to end (profile_dir, extension_path, headless) so tests
    can point at a tmp_path profile dir and the fixture dummy extension
    instead of the real `~/.jobright-autopilot/profile` + JobRight
    extension - exactly the pattern tests/test_persistent_context_extension.py
    already proves works in this sandbox.
    """

    profile_dir: Path = DEFAULT_PROFILE_DIR
    extension_path: Path | None = None
    headless: bool = False
    viewport: dict = field(default_factory=lambda: dict(DEFAULT_VIEWPORT))
    timezone_id: str = DEFAULT_TIMEZONE_ID
    locale: str = DEFAULT_LOCALE
    extra_args: tuple[str, ...] = ()


def build_launch_args(config: BrowserContextConfig) -> list[str]:
    """Chromium launch args for `config`, per S2.1.2.

    Extensions require headed mode or Chromium's "new" headless mode
    (`--headless=new`), and `launch_persistent_context`'s own `headless=`
    kwarg selects a separate `chrome-headless-shell` binary that isn't
    guaranteed to be installed alongside regular Chromium (this sandbox
    only ships the latter) - so "headless" is always expressed as the
    `--headless=new` arg rather than the kwarg, exactly the pattern
    tests/test_persistent_context_extension.py already proves works here.
    """
    args = list(config.extra_args)
    if config.headless and "--headless=new" not in args:
        args.append("--headless=new")
    if config.extension_path is not None:
        ext = str(config.extension_path)
        args.append(f"--disable-extensions-except={ext}")
        args.append(f"--load-extension={ext}")
    return args


def launch_persistent_context(config: BrowserContextConfig, playwright):
    """Create/load the persistent Chromium profile dir (S2.1.1/S2.1.2).

    `playwright` is the object yielded by `sync_playwright()`, passed in
    rather than constructed here so callers control its lifecycle (and can
    reuse it across multiple context launches within one process) - per
    AGENTS.md's preference for small, dependency-injectable functions over
    globals.
    """
    config.profile_dir.mkdir(parents=True, exist_ok=True)
    return playwright.chromium.launch_persistent_context(
        str(config.profile_dir),
        # headlessness is expressed via the `--headless=new` arg (see
        # build_launch_args), not this kwarg - see its docstring for why.
        headless=False,
        accept_downloads=True,  # let the executor capture resume PDF downloads
        viewport=config.viewport,
        timezone_id=config.timezone_id,
        locale=config.locale,
        args=build_launch_args(config),
    )


@contextmanager
def get_context(config: BrowserContextConfig | None = None):
    """Public entrypoint other epics use to obtain a ready persistent
    browser context without knowing about Playwright launch details
    (spec §5 parallelization map: `session.get_context()`).

    Usage:
        with get_context(BrowserContextConfig(profile_dir=..., extension_path=...)) as context:
            page = context.new_page()
            ...
    """
    from playwright.sync_api import sync_playwright

    cfg = config or BrowserContextConfig()
    with sync_playwright() as p:
        context = launch_persistent_context(cfg, p)
        try:
            yield context
        finally:
            context.close()


def get_extension_id(context, *, timeout_ms: int = 10_000) -> str | None:
    """Return the loaded extension's id (S2.1.2 'record extension ID'),
    or None if no extension is configured / none registers in time.

    Checks already-registered service workers first (the extension may
    have registered before this is called), falling back to waiting for
    the `serviceworker` event - the same signal
    tests/test_persistent_context_extension.py asserts on.
    """
    for worker in context.service_workers:
        match = _EXTENSION_ID_RE.match(worker.url)
        if match:
            return match.group(1)
    try:
        worker = context.wait_for_event("serviceworker", timeout=timeout_ms)
    except Exception:
        return None
    match = _EXTENSION_ID_RE.match(worker.url)
    return match.group(1) if match else None


def persist_storage_state(context, *, profile_dir: Path | str = DEFAULT_PROFILE_DIR) -> Path:
    """S2.2.4: persist storage_state.json as a backup alongside the
    persistent profile dir.

    This is belt-and-suspenders - the persistent profile dir already
    persists cookies/local storage on its own - but the spec explicitly
    calls out storage_state.json as a separate backup artifact, and it is
    useful as a portable/inspectable session snapshot independent of the
    full Chromium profile.
    """
    path = Path(profile_dir) / DEFAULT_STORAGE_STATE_FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    context.storage_state(path=str(path))
    return path


def locator_present(
    page, registry: SelectorRegistry, key: str, *, run_id: str, timeout_ms: int = 3000
) -> bool:
    """Best-effort, non-raising presence check for a *resolved* selector.

    Unlike `resolve_locator()` (src/observability/selectors.py), a
    selector that resolves fine but isn't currently visible is NOT
    exceptional here: callers like `is_logged_in()` and the SSO/2FA-
    challenge detectors in src/auth/login.py need to distinguish "this
    element isn't on the page right now" from "this selector is broken" -
    a logged-out user and a challenge screen that never appears are both
    normal states, not selector rot - so that case degrades to `False`.

    A `KeyError` from `registry.get(key)` is a different failure mode
    entirely: the selector key itself is missing or malformed in the
    registry/YAML, which is a structural config break, not a normal page
    state (REV-003). That case is treated exactly like `resolve_locator()`
    treats it - best-effort snapshot + page_url captured, then raised as
    `SelectorBroken` so it routes to a `selector_broken` HITL ticket
    upstream, rather than silently degrading to `False` and masking the
    misconfiguration as "just logged out".
    """
    try:
        selector = registry.get(key)
    except KeyError as exc:
        snapshot_path = None
        try:
            result = snapshot(page, f"selector-broken-{key}", run_id)
            snapshot_path = result.screenshot_path
        except Exception:
            snapshot_path = None
        page_url = None
        try:
            page_url = page.url
        except Exception:
            page_url = None
        raise SelectorBroken(
            key,
            page_url=page_url,
            snapshot_path=snapshot_path,
            original_error=str(exc),
        ) from exc
    try:
        page.locator(selector).first.wait_for(state="visible", timeout=timeout_ms)
        return True
    except Exception:
        return False


def is_logged_in(
    page,
    registry: SelectorRegistry,
    run_id: str,
    *,
    timeout_ms: int = 3000,
    logger=None,
) -> bool:
    """S2.1.3 health check: is the current page an authenticated JobRight
    session? Detects the dashboard indicator vs. the (implicit) absence of
    it - being logged out is a normal state, so a resolved-but-invisible
    indicator degrades to False rather than raising. A structurally broken
    `login.dashboard_indicator` registry entry is NOT a normal state,
    though - `locator_present()` raises `SelectorBroken` for that case
    (REV-003), and this function deliberately does not catch it.
    """
    logged_in = locator_present(
        page, registry, "login.dashboard_indicator", run_id=run_id, timeout_ms=timeout_ms
    )
    if logger is not None:
        log_event(logger, "login_health_check", run_id=run_id, logged_in=logged_in)
    return logged_in
