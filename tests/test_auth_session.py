"""Tests for src/auth/session.py (spec T2.3): SessionManager auto-relogin,
backoff+jitter, hard-stop via LoginFailed, and the is_extension_authenticated
stub.

Pure unit tests - no Playwright browser needed. `SessionManager.login_fn`,
`is_logged_in_fn`, `sleep_fn`, and `jitter_fn` are all dependency-injected
constructor fields, so fake login/health-check outcomes and a non-sleeping
sleep_fn are used throughout (never sleep for real in tests). The
orchestrator added `is_logged_in_fn` as a proper injectable field after this
suite first shipped monkeypatching `src.auth.session.is_logged_in` directly
(see PROGRESS.md) - using the field directly is simpler and doesn't depend
on module-global lookup timing.
"""

from __future__ import annotations

import pytest
from src.auth.login import LoginOutcome
from src.auth.session import SessionManager, is_extension_authenticated
from src.config.secrets import Secrets
from src.contracts.exceptions import LoginFailed
from src.observability.selectors import SelectorRegistry
from src.tracker.repository import InMemoryTrackerRepository


def make_manager(
    monkeypatch,
    *,
    is_logged_in_result,
    login_fn,
    max_failures=3,
    backoff_base_seconds=2.0,
    jitter_value=0.5,
):
    sleeps: list[float] = []
    manager = SessionManager(
        registry=SelectorRegistry({}),
        secrets=Secrets(jobright_email="a@example.com", jobright_password="hunter2"),
        tracker=InMemoryTrackerRepository(),
        run_id="run-session-1",
        max_failures=max_failures,
        backoff_base_seconds=backoff_base_seconds,
        login_fn=login_fn,
        is_logged_in_fn=lambda *a, **k: is_logged_in_result,
        sleep_fn=sleeps.append,
        jitter_fn=lambda: jitter_value,
    )
    return manager, sleeps


# -- ensure_logged_in ----------------------------------------------------


def test_ensure_logged_in_skips_relogin_when_already_authenticated(monkeypatch):
    login_calls = []

    def fake_login(*a, **k):
        login_calls.append(1)
        return LoginOutcome(status="success")

    manager, sleeps = make_manager(monkeypatch, is_logged_in_result=True, login_fn=fake_login)

    outcome = manager.ensure_logged_in(page=object())

    assert outcome == LoginOutcome(status="success")
    assert login_calls == []  # relogin() must not have been invoked at all
    assert manager.consecutive_failures == 0
    assert sleeps == []


def test_ensure_logged_in_triggers_relogin_when_session_expired(monkeypatch):
    login_calls = []

    def fake_login(*a, **k):
        login_calls.append(1)
        return LoginOutcome(status="success")

    manager, sleeps = make_manager(monkeypatch, is_logged_in_result=False, login_fn=fake_login)

    outcome = manager.ensure_logged_in(page=object())

    assert outcome == LoginOutcome(status="success")
    assert len(login_calls) == 1
    assert manager.consecutive_failures == 0


def test_ensure_logged_in_resets_failure_counter_once_session_is_healthy_again(monkeypatch):
    # Seed some prior failures, then simulate a healthy session on the next
    # ensure_logged_in() call - the counter must reset to 0.
    manager, sleeps = make_manager(
        monkeypatch,
        is_logged_in_result=True,
        login_fn=lambda *a, **k: LoginOutcome(status="success"),
    )
    manager._consecutive_failures = 2  # simulate accumulated prior failures

    outcome = manager.ensure_logged_in(page=object())

    assert outcome.status == "success"
    assert manager.consecutive_failures == 0


# -- relogin: backoff+jitter growth and hard-stop -------------------------


def test_relogin_backoff_grows_and_hard_stops_after_max_failures(monkeypatch):
    login_calls = []

    def failing_login(*a, **k):
        login_calls.append(1)
        return LoginOutcome(status="failed", error="bad creds")

    manager, sleeps = make_manager(
        monkeypatch,
        is_logged_in_result=False,
        login_fn=failing_login,
        max_failures=3,
        backoff_base_seconds=2.0,
        jitter_value=0.5,
    )

    with pytest.raises(LoginFailed) as excinfo:
        manager.relogin(page=object())

    assert excinfo.value.attempts == 3
    assert excinfo.value.max_failures == 3
    # login_fn was called exactly max_failures times (3), never a 4th time.
    assert len(login_calls) == 3
    # sleep_fn was only called after the 1st and 2nd failures (2 delays) -
    # the 3rd failure hits the hard-stop before any delay is computed, so
    # sleep_fn is NOT called a 3rd time either.
    assert sleeps == [2.5, 4.5]
    assert sleeps[1] > sleeps[0]
    assert manager.consecutive_failures == 3


def test_relogin_hitl_pending_does_not_count_as_failure_or_sleep(monkeypatch):
    manager, sleeps = make_manager(
        monkeypatch,
        is_logged_in_result=False,
        login_fn=lambda *a, **k: LoginOutcome(status="hitl_pending", ticket_id="t1"),
    )

    outcome = manager.relogin(page=object())

    assert outcome == LoginOutcome(status="hitl_pending", ticket_id="t1")
    assert manager.consecutive_failures == 0
    assert sleeps == []


def test_relogin_recovers_and_resets_failure_counter_after_eventual_success(monkeypatch):
    outcomes = iter(
        [
            LoginOutcome(status="failed", error="e1"),
            LoginOutcome(status="success"),
        ]
    )

    def flaky_login(*a, **k):
        return next(outcomes)

    manager, sleeps = make_manager(
        monkeypatch,
        is_logged_in_result=False,
        login_fn=flaky_login,
        max_failures=5,
        backoff_base_seconds=2.0,
        jitter_value=0.5,
    )

    outcome = manager.relogin(page=object())

    assert outcome.status == "success"
    assert manager.consecutive_failures == 0
    assert sleeps == [2.5]  # exactly one backoff delay between the two attempts


def test_relogin_raises_immediately_if_already_at_max_failures(monkeypatch):
    """If the failure budget is already exhausted (e.g. a caller re-enters
    relogin() after a prior LoginFailed was somehow swallowed), relogin()
    must hard-stop before calling login_fn/sleep_fn at all.
    """
    login_calls = []

    def fake_login(*a, **k):
        login_calls.append(1)
        return LoginOutcome(status="success")

    manager, sleeps = make_manager(
        monkeypatch, is_logged_in_result=False, login_fn=fake_login, max_failures=2
    )
    manager._consecutive_failures = 2

    with pytest.raises(LoginFailed):
        manager.relogin(page=object())

    assert login_calls == []
    assert sleeps == []


# -- is_extension_authenticated stub --------------------------------------


def test_is_extension_authenticated_stub_returns_none():
    assert is_extension_authenticated(context=object(), extension_id="abc123") is None
