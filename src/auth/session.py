"""Session lifecycle: auto-relogin on expiry, backoff+jitter, hard-stop
after repeated login failures (spec T2.3).

`SessionManager` is the single stateful piece in this package (tracking
consecutive-failure count across calls within a run); everything it calls
into (`is_logged_in`, `login`) stays a plain, dependency-injectable
function per AGENTS.md's "small, testable functions" rule.
"""

from __future__ import annotations

import random
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from src.auth.context import DEFAULT_PROFILE_DIR, is_logged_in
from src.auth.login import SESSION_JOB_ID, LoginOutcome, login
from src.config.secrets import Secrets
from src.contracts.exceptions import LoginFailed
from src.observability.logging import log_event
from src.observability.selectors import SelectorRegistry


@dataclass
class SessionManager:
    """Keeps a Playwright page's JobRight session alive across a run.

    S2.3.1: `ensure_logged_in()` re-runs the login flow whenever
    `is_logged_in()` reports the session has expired (e.g. a mid-run
    navigation redirected to the login page) - this is the "navigation
    guard" the spec describes, invoked by callers before/around each
    stage rather than a background watcher, keeping this synchronous and
    simple to test.

    S2.3.3: repeated login failures back off with jitter and hard-stop
    after `max_failures` consecutive failures, raising `LoginFailed`
    rather than continuing to hammer the login endpoint. A HITL handoff
    (`hitl_pending`) does NOT count as a failure - a human is already in
    the loop, so it isn't "hammering" anything.

    `login_fn`, `is_logged_in_fn`, `sleep_fn`, and `jitter_fn` are all
    injectable so a Testing agent can substitute a fake login/health-check
    outcome or a no-op sleep without a real browser or real timing, per
    AGENTS.md's testability rule.
    """

    registry: SelectorRegistry
    secrets: Secrets
    tracker: object  # TrackerRepository-shaped: needs add_ticket()/get_ticket()
    run_id: str
    max_failures: int = 3
    backoff_base_seconds: float = 2.0
    logger: object | None = None
    profile_dir: Path = DEFAULT_PROFILE_DIR
    login_fn: Callable[..., LoginOutcome] = login
    is_logged_in_fn: Callable[..., bool] = is_logged_in
    sleep_fn: Callable[[float], None] = time.sleep
    jitter_fn: Callable[[], float] = random.random

    _consecutive_failures: int = field(default=0, init=False, repr=False)

    @property
    def consecutive_failures(self) -> int:
        return self._consecutive_failures

    def ensure_logged_in(self, page, *, job_id: str = SESSION_JOB_ID) -> LoginOutcome:
        """Return success immediately if already logged in; otherwise
        drive a full (retrying) relogin attempt.
        """
        if self.is_logged_in_fn(page, self.registry, self.run_id, logger=self.logger):
            self._consecutive_failures = 0
            return LoginOutcome(status="success")
        return self.relogin(page, job_id=job_id)

    def relogin(self, page, *, job_id: str = SESSION_JOB_ID) -> LoginOutcome:
        """Attempt to log back in, retrying with exponential backoff+jitter
        on failure, hard-stopping with `LoginFailed` once
        `max_failures` consecutive failures have accumulated (S2.3.3).
        """
        while True:
            if self._consecutive_failures >= self.max_failures:
                raise LoginFailed(self._consecutive_failures, self.max_failures)

            outcome = self.login_fn(
                page,
                self.registry,
                self.secrets,
                self.tracker,
                self.run_id,
                job_id=job_id,
                logger=self.logger,
                profile_dir=self.profile_dir,
            )

            if outcome.status == "success":
                self._consecutive_failures = 0
                return outcome

            if outcome.status == "hitl_pending":
                # A human is now in the loop; don't burn the failure
                # budget or back off - the caller awaits ticket
                # resolution and re-checks separately.
                return outcome

            self._consecutive_failures += 1
            if self.logger is not None:
                log_event(
                    self.logger,
                    "relogin_attempt_failed",
                    level=30,
                    run_id=self.run_id,
                    job_id=job_id,
                    attempt=self._consecutive_failures,
                    max_failures=self.max_failures,
                    error=outcome.error,
                )

            if self._consecutive_failures >= self.max_failures:
                raise LoginFailed(self._consecutive_failures, self.max_failures)

            delay = self.backoff_base_seconds * (2 ** (self._consecutive_failures - 1))
            delay += self.jitter_fn()
            self.sleep_fn(delay)


def is_extension_authenticated(context, extension_id: str | None) -> bool | None:
    """S2.3.2 stub/TODO - deliberately NOT implemented beyond this
    documented stub, per the task's own guidance ("Extension-authenticated
    check can be a stub/TODO ... do not over-build speculative code for
    it").

    There is no live JobRight extension or account in this environment, so
    the extension's actual auth surface (popup DOM? badge text via
    `chrome.action`? a background-page message?) is unknown and would be
    speculative to implement now.

    TODO (needs the real JobRight extension + a live account): once
    available, open `chrome-extension://<extension_id>/popup.html` as a
    page within the persistent context (see
    `src.auth.context.get_extension_id` for how to obtain `extension_id`)
    and inspect its DOM/badge state for a logged-in indicator; if the
    extension turns out to have its own separate login step, open a
    one-time-setup HITL ticket the same way `src.auth.login` does for
    SSO/2FA.

    Returns None ("unknown") rather than guessing True/False so callers
    must treat "unknown" as a distinct third state instead of silently
    coercing it to a boolean.
    """
    return None
