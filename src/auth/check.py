"""Standalone login health-check entrypoint (spec Epic 2 DoD).

The Epic 2 DoD names `python -m src.auth.check` as the command that
"reliably returns logged-in state across restarts... without re-entering
credentials". That entrypoint never shipped with the original T2.1/T2.3
implementation (flagged as REV-001 by the continuous review in
review.md) - this module closes that gap.

Usage:
    python -m src.auth.check
    python -m src.auth.check --profile-dir ~/.jobright-autopilot/profile

Exit code 0 means an authenticated session was detected; 1 means logged
out (or the check itself could not run). No live jobright.ai access
exists in this sandbox (no credentials), so `check_login_state()` is
built to be independently testable against local fixture pages - see
tests/test_auth_check.py - rather than only exercisable live.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path

from src.auth.context import BrowserContextConfig, get_context
from src.observability.logging import configure_logging, log_event
from src.observability.run_id import new_run_id
from src.observability.selectors import DEFAULT_SELECTORS_PATH, SelectorRegistry

JOBRIGHT_BASE_URL = "https://jobright.ai"
DASHBOARD_PATH = "/jobs/recommended"


@dataclass
class CheckResult:
    run_id: str
    logged_in: bool
    profile_dir: str
    base_url: str


def check_login_state(
    config: BrowserContextConfig | None = None,
    *,
    base_url: str = JOBRIGHT_BASE_URL,
    dashboard_path: str = DASHBOARD_PATH,
    registry_path: str | Path = DEFAULT_SELECTORS_PATH,
    run_id: str | None = None,
    timeout_ms: int = 8000,
) -> CheckResult:
    """Launch the persistent profile, navigate to the dashboard URL, and
    report whether `is_logged_in()` detects an authenticated session.

    `base_url`/`dashboard_path` are overridable so tests can point this at
    a local fixture page instead of the real site (no live access here).
    """
    from src.auth.context import is_logged_in

    cfg = config or BrowserContextConfig()
    rid = run_id or new_run_id()
    registry = SelectorRegistry.load(registry_path)

    with get_context(cfg) as context:
        page = context.new_page()
        try:
            page.goto(f"{base_url}{dashboard_path}")
            logged_in = is_logged_in(page, registry, rid, timeout_ms=timeout_ms)
        finally:
            page.close()

    return CheckResult(
        run_id=rid,
        logged_in=logged_in,
        profile_dir=str(cfg.profile_dir),
        base_url=base_url,
    )


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="python -m src.auth.check")
    parser.add_argument(
        "--profile-dir",
        type=Path,
        default=BrowserContextConfig().profile_dir,
        help="Persistent Chromium profile directory to check (default: %(default)s)",
    )
    parser.add_argument(
        "--base-url",
        default=JOBRIGHT_BASE_URL,
        help="Base URL to navigate to before checking login state (default: %(default)s)",
    )
    args = parser.parse_args(argv)

    run_id = new_run_id()
    logger = configure_logging(run_id)
    try:
        result = check_login_state(
            BrowserContextConfig(profile_dir=args.profile_dir),
            base_url=args.base_url,
            run_id=run_id,
        )
    except Exception as exc:
        # Per this module's own docstring, exit code 1 also covers a check
        # that "could not run" (browser launch, selector-load, navigation
        # failure, etc.) - previously these escaped as a raw traceback
        # instead of the documented structured-failure contract (REV-005).
        log_event(logger, "auth_check_failed", level=40, error=str(exc))
        print(json.dumps({"run_id": run_id, "logged_in": False, "error": str(exc)}, indent=2))
        return 1

    logger.info("auth_check_complete", extra={"job_id": None, "step": "auth_check"})
    print(json.dumps(result.__dict__, indent=2))
    return 0 if result.logged_in else 1


if __name__ == "__main__":
    sys.exit(main())
