"""Tests for src/auth/login.py (spec T2.2): SSO/2FA/challenge detection,
credentials login end-to-end, HITL ticket creation, and the
login()/await_ticket_resolution() orchestration.

No live jobright.ai access exists in this environment - login form/SSO
button/challenge screen are all simulated via local `data:` URL fixture
pages, exactly like tests/test_selector_broken.py does for Epic 1.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import sync_playwright
from src.auth.login import (
    LoginOutcome,
    await_ticket_resolution,
    detect_challenge_screen,
    login,
    login_with_credentials,
    open_login_hitl_ticket,
    sso_detected,
)
from src.config.secrets import Secrets
from src.contracts.exceptions import SelectorBroken
from src.contracts.models import HITLTicket
from src.observability.selectors import SelectorRegistry
from src.tracker.repository import InMemoryTrackerRepository

pytestmark = pytest.mark.playwright

REGISTRY = SelectorRegistry(
    {
        "login": {
            "email_input": "#email",
            "password_input": "#password",
            "submit_button": "#submit",
            "sso_google_button": "#sso-google",
            "dashboard_indicator": "#dashboard",
            "challenge_indicator": "#challenge",
        }
    }
)

# A login form that swaps its own DOM for a "dashboard" element (containing
# the filled email, so the test can prove the fields were actually filled
# before submit) via a tiny inline <script>, since there's no real
# jobright.ai to submit credentials to.
LOGIN_FORM_HTML = (
    "<html><body>"
    "<input id='email' type='email'>"
    "<input id='password' type='password'>"
    "<button id='submit' type='button' onclick='submitLogin()'>Submit</button>"
    "<script>"
    "function submitLogin() {"
    "  var e = document.getElementById('email').value;"
    '  document.body.innerHTML = "<div id=\'dashboard\'>Welcome " + e + "</div>";'
    "}"
    "</script>"
    "</body></html>"
)

# Same shape, but the click handler never produces a dashboard indicator -
# simulates credentials being submitted but rejected/hanging.
LOGIN_FORM_NEVER_SUCCEEDS_HTML = (
    "<html><body>"
    "<input id='email' type='email'>"
    "<input id='password' type='password'>"
    "<button id='submit' type='button' onclick='void(0)'>Submit</button>"
    "</body></html>"
)


def make_secrets(**overrides) -> Secrets:
    defaults = dict(jobright_email="a@example.com", jobright_password="hunter2")
    defaults.update(overrides)
    return Secrets(**defaults)


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True, channel="chromium")
        yield b
        b.close()


# -- sso_detected -------------------------------------------------------------


def test_sso_detected_true_via_secrets_flag(browser):
    page = browser.new_page()
    page.goto("data:text/html,<html><body>no sso button here</body></html>")
    secrets = make_secrets(jobright_sso=True)
    assert sso_detected(page, REGISTRY, secrets, run_id="run-sso-1", timeout_ms=200) is True
    page.close()


def test_sso_detected_true_via_button_presence(browser):
    page = browser.new_page()
    page.goto(
        "data:text/html,<html><body><button id='sso-google'>Sign in with Google</button>"
        "</body></html>"
    )
    secrets = make_secrets()
    assert sso_detected(page, REGISTRY, secrets, run_id="run-sso-2", timeout_ms=500) is True
    page.close()


def test_sso_detected_false_when_absent(browser):
    page = browser.new_page()
    page.goto("data:text/html,<html><body>plain login page</body></html>")
    secrets = make_secrets()
    assert sso_detected(page, REGISTRY, secrets, run_id="run-sso-3", timeout_ms=200) is False
    page.close()


# -- detect_challenge_screen --------------------------------------------------


def test_detect_challenge_screen_true_when_present(browser):
    page = browser.new_page()
    page.goto("data:text/html,<html><body><div id='challenge'>Enter code</div></body></html>")
    assert detect_challenge_screen(page, REGISTRY, run_id="run-challenge-1", timeout_ms=500) is True
    page.close()


def test_detect_challenge_screen_false_when_absent(browser):
    page = browser.new_page()
    page.goto("data:text/html,<html><body>no challenge here</body></html>")
    assert (
        detect_challenge_screen(page, REGISTRY, run_id="run-challenge-2", timeout_ms=200) is False
    )
    page.close()


# -- open_login_hitl_ticket ----------------------------------------------------


def test_open_login_hitl_ticket_creates_login_2fa_ticket(tmp_path, monkeypatch, browser):
    monkeypatch.chdir(tmp_path)  # snapshot() writes under ./runs/<run_id>/
    tracker = InMemoryTrackerRepository()
    page = browser.new_page()
    page.goto("data:text/html,<html><body>challenge</body></html>")

    outcome = open_login_hitl_ticket(tracker, page, "run-hitl-1", reason="test_reason")

    assert outcome.status == "hitl_pending"
    assert outcome.ticket_id is not None

    tickets = tracker.list_tickets()
    assert len(tickets) == 1
    assert tickets[0].kind == "login_2fa"
    assert tickets[0].ticket_id == outcome.ticket_id
    assert tracker.get_ticket(outcome.ticket_id) is tickets[0]
    page.close()


# -- login_with_credentials (end-to-end against a fixture form) --------------


def test_login_with_credentials_succeeds_end_to_end(browser):
    page = browser.new_page()
    page.goto("data:text/html," + LOGIN_FORM_HTML)
    secrets = make_secrets()

    login_with_credentials(page, REGISTRY, secrets, "run-login-1", timeout_ms=2000)

    # Dashboard indicator now present, and it carries the email that was
    # actually typed into the (now-replaced) #email field, proving the
    # fill() calls happened before the click swapped the DOM.
    dashboard = page.locator("#dashboard")
    dashboard.wait_for(state="visible", timeout=1000)
    assert "a@example.com" in dashboard.inner_text()
    page.close()


def test_login_with_credentials_raises_selector_broken_on_missing_form(
    tmp_path, monkeypatch, browser
):
    monkeypatch.chdir(tmp_path)
    page = browser.new_page()
    page.goto("data:text/html,<html><body>totally blank page</body></html>")
    secrets = make_secrets()

    with pytest.raises(SelectorBroken) as excinfo:
        login_with_credentials(page, REGISTRY, secrets, "run-login-broken", timeout_ms=300)

    assert excinfo.value.selector_key == "login.email_input"
    page.close()


# -- await_ticket_resolution ---------------------------------------------------


def test_await_ticket_resolution_returns_true_once_resolved():
    tracker = InMemoryTrackerRepository()
    ticket = HITLTicket(ticket_id="t-resolve", job_id="_session", kind="login_2fa", context={})
    tracker.add_ticket(ticket)

    calls = {"n": 0}

    def sleep_fn(_delay):
        calls["n"] += 1
        if calls["n"] == 1:
            resolved = tracker.get_ticket("t-resolve").model_copy(update={"resolution": "approved"})
            tracker.add_ticket(resolved)

    result = await_ticket_resolution(
        tracker,
        "t-resolve",
        timeout_s=100,
        poll_interval_s=0.01,
        sleep_fn=sleep_fn,
        now_fn=lambda: 0.0,
    )
    assert result is True
    assert calls["n"] == 1


def test_await_ticket_resolution_times_out_and_returns_false():
    tracker = InMemoryTrackerRepository()
    ticket = HITLTicket(ticket_id="t-timeout", job_id="_session", kind="login_2fa", context={})
    tracker.add_ticket(ticket)

    times = iter([0.0, 1.0, 2.0, 10.0])
    sleeps = []

    result = await_ticket_resolution(
        tracker,
        "t-timeout",
        timeout_s=5,
        poll_interval_s=1,
        sleep_fn=sleeps.append,
        now_fn=lambda: next(times),
    )
    assert result is False
    assert sleeps == [1, 1]


# -- login() full orchestration ------------------------------------------------


def test_login_success_end_to_end(tmp_path, monkeypatch, browser):
    monkeypatch.chdir(tmp_path)

    page = browser.new_page()
    page.goto("data:text/html," + LOGIN_FORM_HTML)
    secrets = make_secrets()
    tracker = InMemoryTrackerRepository()

    # profile_dir is threaded through explicitly (the orchestrator fixed
    # login() to accept it rather than always falling back to the
    # module-level DEFAULT_PROFILE_DIR - see PROGRESS.md), so this writes
    # storage_state.json under tmp_path instead of the real
    # ~/.jobright-autopilot/profile on the host running the suite.
    profile_dir = tmp_path / "profile"
    outcome = login(page, REGISTRY, secrets, tracker, "run-login-2", profile_dir=profile_dir)

    assert outcome == LoginOutcome(status="success")
    assert (profile_dir / "storage_state.json").exists()
    assert tracker.list_tickets() == []
    page.close()


def test_login_sso_detected_opens_hitl_ticket_and_does_not_submit_credentials(
    tmp_path, monkeypatch, browser
):
    monkeypatch.chdir(tmp_path)
    page = browser.new_page()
    page.goto(
        "data:text/html,<html><body><button id='sso-google'>Sign in with Google</button>"
        "</body></html>"
    )
    secrets = make_secrets()
    tracker = InMemoryTrackerRepository()

    outcome = login(page, REGISTRY, secrets, tracker, "run-login-sso")

    assert outcome.status == "hitl_pending"
    assert outcome.ticket_id is not None
    tickets = tracker.list_tickets()
    assert len(tickets) == 1
    assert tickets[0].kind == "login_2fa"
    assert tickets[0].context["reason"] == "sso_google_detected"
    page.close()


def test_login_challenge_screen_detected_opens_hitl_ticket(tmp_path, monkeypatch, browser):
    monkeypatch.chdir(tmp_path)
    page = browser.new_page()
    page.goto("data:text/html,<html><body><div id='challenge'>Enter code</div></body></html>")
    secrets = make_secrets()
    tracker = InMemoryTrackerRepository()

    outcome = login(page, REGISTRY, secrets, tracker, "run-login-challenge")

    assert outcome.status == "hitl_pending"
    tickets = tracker.list_tickets()
    assert len(tickets) == 1
    assert tickets[0].context["reason"] == "challenge_screen_detected"
    page.close()


def test_login_propagates_selector_broken_when_login_form_missing(tmp_path, monkeypatch, browser):
    monkeypatch.chdir(tmp_path)
    page = browser.new_page()
    page.goto("data:text/html,<html><body>totally blank page</body></html>")
    secrets = make_secrets()
    tracker = InMemoryTrackerRepository()

    with pytest.raises(SelectorBroken):
        login(page, REGISTRY, secrets, tracker, "run-login-form-missing", timeout_ms=300)
    page.close()


def test_login_returns_failed_when_dashboard_never_appears(tmp_path, monkeypatch, browser):
    monkeypatch.chdir(tmp_path)
    page = browser.new_page()
    page.goto("data:text/html," + LOGIN_FORM_NEVER_SUCCEEDS_HTML)
    secrets = make_secrets()
    tracker = InMemoryTrackerRepository()

    outcome = login(page, REGISTRY, secrets, tracker, "run-login-4", timeout_ms=300)

    assert outcome.status == "failed"
    assert outcome.error is not None
    assert tracker.list_tickets() == []
    page.close()
