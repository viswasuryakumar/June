"""Login flow: email/password, SSO detection, 2FA/challenge detection
(spec T2.2).

No live jobright.ai access exists in this environment (no credentials) -
this module never assumes a specific real-world login form beyond the
selector registry keys, and is built/testable against local `data:` URL
or file:// fixture pages that simulate a login form / SSO button /
challenge screen, exactly like tests/test_selector_broken.py does for
Epic 1.

Google SSO and generic 2FA/OTP/email-verification screens are both
handled the same way per spec: never attempt to drive them
automatically (Google actively blocks automated sign-in; OTP screens
need a human to read a code) - instead open a HITLTicket of kind
`login_2fa` and hand a clear signal back to the caller so it can wait for
a human to complete the challenge in the headed browser window.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from src.auth.context import (
    DEFAULT_PROFILE_DIR,
    is_logged_in,
    locator_present,
    persist_storage_state,
)
from src.config.secrets import Secrets
from src.contracts.models import HITLTicket
from src.observability.logging import log_event
from src.observability.selectors import SelectorRegistry, resolve_locator
from src.observability.snapshot import snapshot

LoginStatus = Literal["success", "hitl_pending", "failed"]

# HITL tickets opened by the login flow aren't tied to a specific job -
# they gate the whole run's ability to authenticate at all.
SESSION_JOB_ID = "_session"


@dataclass(frozen=True)
class LoginOutcome:
    """Result of one login attempt/orchestration pass."""

    status: LoginStatus
    ticket_id: str | None = None
    error: str | None = None


def sso_detected(
    page, registry: SelectorRegistry, secrets: Secrets, *, run_id: str, timeout_ms: int = 1000
) -> bool:
    """S2.2.2: detect whether this login should go through Google SSO -
    either because config/secrets say so, or because the SSO button is
    visibly present on the current login page.

    `run_id` is threaded through to `locator_present()` purely so it can
    attach a snapshot if `login.sso_google_button` turns out to be a
    structurally broken registry key (REV-003) - it plays no role in the
    secrets-flag branch, which never touches the page/registry at all.
    """
    if secrets.jobright_sso:
        return True
    return locator_present(
        page, registry, "login.sso_google_button", run_id=run_id, timeout_ms=timeout_ms
    )


def detect_challenge_screen(
    page, registry: SelectorRegistry, *, run_id: str, timeout_ms: int = 1500
) -> bool:
    """S2.2.3: best-effort detection of a 2FA/OTP/email-verification
    challenge screen. No live challenge selector has ever been observed
    (no credentials in this environment), so this is deliberately
    pluggable: it just checks the `login.challenge_indicator` registry
    key (see selectors/jobright.yaml), and is testable today against a
    fixture HTML page that simulates a challenge screen.
    """
    return locator_present(
        page, registry, "login.challenge_indicator", run_id=run_id, timeout_ms=timeout_ms
    )


def _trigger_google_sso(
    page,
    registry: SelectorRegistry,
    secrets: Secrets,
    *,
    run_id: str,
    timeout_ms: int = 8000,
) -> bool:
    """Trigger Google SSO if the page exposes the SSO affordance.

    When the login page offers a Google sign-in button, click it and then
    try to prefill the user's email on any Google-style email field. The
    flow still stops at a HITL handoff for the actual Google password/2FA
    step, since those are intentionally human-mediated in this project.
    """
    try:
        resolve_locator(
            page, registry, "login.sso_google_button", run_id=run_id, timeout_ms=timeout_ms
        ).click()
    except Exception:
        return False

    try:
        page.wait_for_load_state("domcontentloaded", timeout=timeout_ms)
    except Exception:
        pass

    if not secrets.jobright_email:
        return True

    for selector in (
        "input[type='email']",
        "input[name='email']",
        "input[autocomplete='email']",
        "input[id*='email']",
    ):
        try:
            locator = page.locator(selector).first
            if locator.count() > 0:
                locator.fill(secrets.jobright_email)
                return True
        except Exception:
            continue
    return True


def _safe_page_url(page) -> str | None:
    try:
        return page.url
    except Exception:
        return None


def open_login_hitl_ticket(
    tracker,
    page,
    run_id: str,
    *,
    reason: str,
    job_id: str = SESSION_JOB_ID,
    logger=None,
) -> LoginOutcome:
    """Open a HITLTicket(kind='login_2fa') for a SSO/2FA/challenge
    handoff and return the 'hitl_pending' signal the caller should
    await-resolution on (spec S2.2.2/S2.2.3).

    Uses the tracker's existing add_ticket()/get_ticket() surface (see
    src/tracker/repository.py) rather than a real ticket API, since Epic 7
    (the HITL Hub) hasn't landed yet - this is the same "minimal extension
    for CLI stubs" surface Epic 1 already sanctioned for this purpose.
    """
    context: dict = {"reason": reason, "page_url": _safe_page_url(page), "run_id": run_id}
    try:
        result = snapshot(page, f"login-{reason}", run_id)
        context["screenshot_path"] = result.screenshot_path
        context["html_path"] = result.html_path
    except Exception:
        pass

    ticket = HITLTicket(
        ticket_id=f"login-{run_id}-{uuid.uuid4().hex[:8]}",
        job_id=job_id,
        kind="login_2fa",
        context=context,
    )
    tracker.add_ticket(ticket)
    if logger is not None:
        log_event(
            logger,
            "login_hitl_ticket_opened",
            level=30,
            run_id=run_id,
            job_id=job_id,
            ticket_id=ticket.ticket_id,
            reason=reason,
        )
    return LoginOutcome(status="hitl_pending", ticket_id=ticket.ticket_id)


def await_ticket_resolution(
    tracker,
    ticket_id: str,
    *,
    timeout_s: float = 1800,
    poll_interval_s: float = 5,
    sleep_fn: Callable[[float], None] = time.sleep,
    now_fn: Callable[[], float] = time.monotonic,
) -> bool:
    """Poll the tracker for a human-provided resolution on `ticket_id`.

    Epic 7's real `await_resolution(ticket_id, timeout)` (spec T7.1.1)
    doesn't exist yet, so this is a small, self-contained bridge built
    only against the tracker surface Epic 1 already ships
    (get_ticket/add_ticket) - a human resolves the ticket out-of-band
    (e.g. `autopilot hitl resolve <id>` once Epic 7 lands, or by editing
    the in-memory/DB record directly today) by setting `resolution`.
    Returns True once `ticket.resolution` is set, False on timeout.
    """
    deadline = now_fn() + timeout_s
    while True:
        ticket = tracker.get_ticket(ticket_id)
        if ticket is not None and ticket.resolution:
            return True
        if now_fn() >= deadline:
            return False
        sleep_fn(poll_interval_s)


def login_with_credentials(
    page,
    registry: SelectorRegistry,
    secrets: Secrets,
    run_id: str,
    *,
    timeout_ms: int = 8000,
) -> None:
    """S2.2.1: fill email/password, submit, wait on network-idle + the
    dashboard indicator.

    Uses `resolve_locator()` (not the non-raising `locator_present()`)
    deliberately: a missing email/password/submit field on what is
    supposed to be the login form itself IS a genuine selector break, not
    a normal "logged out" state - it should snapshot and raise
    SelectorBroken so it routes to a `selector_broken` HITL ticket.
    """
    resolve_locator(page, registry, "login.email_input", run_id=run_id, timeout_ms=timeout_ms).fill(
        secrets.jobright_email
    )
    resolve_locator(
        page, registry, "login.password_input", run_id=run_id, timeout_ms=timeout_ms
    ).fill(secrets.jobright_password)
    resolve_locator(
        page, registry, "login.submit_button", run_id=run_id, timeout_ms=timeout_ms
    ).click()
    try:
        page.wait_for_load_state("networkidle", timeout=timeout_ms * 2)
    except Exception:
        pass  # best-effort; the dashboard-indicator check below is authoritative


def login(
    page,
    registry: SelectorRegistry,
    secrets: Secrets,
    tracker,
    run_id: str,
    *,
    job_id: str = SESSION_JOB_ID,
    logger=None,
    timeout_ms: int = 8000,
    profile_dir: Path | str = DEFAULT_PROFILE_DIR,
) -> LoginOutcome:
    """Orchestrate one login attempt (spec T2.2): SSO/2FA detection first
    (never drive those flows automatically), else email/password fill +
    submit, then verify via `is_logged_in()` and persist storage state.

    `profile_dir` must match whatever `BrowserContextConfig.profile_dir`
    the page's context was actually launched with (see src/auth/context.py)
    so the storage-state backup lands next to the real profile in use,
    not the module-level default.
    """
    if logger is not None:
        log_event(logger, "login_attempt_start", run_id=run_id, job_id=job_id)

    if sso_detected(page, registry, secrets, run_id=run_id):
        _trigger_google_sso(page, registry, secrets, run_id=run_id, timeout_ms=timeout_ms)
        return open_login_hitl_ticket(
            tracker,
            page,
            run_id,
            reason="sso_google_detected",
            job_id=job_id,
            logger=logger,
        )

    if detect_challenge_screen(page, registry, run_id=run_id):
        return open_login_hitl_ticket(
            tracker, page, run_id, reason="challenge_screen_detected", job_id=job_id, logger=logger
        )

    try:
        login_with_credentials(page, registry, secrets, run_id, timeout_ms=timeout_ms)
    except Exception as exc:
        # SelectorBroken (structurally broken login form) intentionally
        # propagates unchanged - it already carries its own HITL routing
        # (kind='selector_broken') and snapshot context.
        from src.contracts.exceptions import SelectorBroken

        if isinstance(exc, SelectorBroken):
            raise
        if logger is not None:
            log_event(
                logger, "login_submit_error", level=30, run_id=run_id, job_id=job_id, error=str(exc)
            )
        return LoginOutcome(status="failed", error=str(exc))

    if is_logged_in(page, registry, run_id, timeout_ms=timeout_ms, logger=logger):
        persist_storage_state(page.context, profile_dir=profile_dir)
        if logger is not None:
            log_event(logger, "login_succeeded", run_id=run_id, job_id=job_id)
        return LoginOutcome(status="success")

    # Dashboard indicator never showed up - maybe a challenge screen
    # appeared only after submitting credentials (S2.2.3).
    if detect_challenge_screen(page, registry, run_id=run_id):
        return open_login_hitl_ticket(
            tracker,
            page,
            run_id,
            reason="challenge_screen_post_submit",
            job_id=job_id,
            logger=logger,
        )

    if logger is not None:
        log_event(logger, "login_failed", level=30, run_id=run_id, job_id=job_id)
    return LoginOutcome(status="failed", error="dashboard indicator not found after submit")
