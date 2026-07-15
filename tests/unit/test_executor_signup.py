"""Component tests for src/executor/signup.py against local data: URL
fixtures (same Playwright pattern as test_executor_forms.py)."""

from __future__ import annotations

import pytest
from playwright.sync_api import sync_playwright
from src.executor.signup import (
    fill_email_fields,
    find_register_control,
    find_signin_control,
    page_has_password_field,
    set_password_fields,
    signup_email,
    signup_password,
)

pytestmark = pytest.mark.playwright


@pytest.fixture(scope="module")
def page():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, channel="chromium")
        page = browser.new_page()
        yield page
        browser.close()


# --- credential resolution (no browser) ---------------------------------


class TestCredentialResolution:
    def test_password_env_wins(self, tmp_path):
        secrets = tmp_path / "secrets.yaml"
        secrets.write_text("signup_password: from_file\n", encoding="utf-8")
        env = {"APPLY_SIGNUP_PASSWORD": "from_env"}
        assert signup_password(env=env, secrets_path=secrets) == "from_env"

    def test_password_falls_back_to_secrets_file(self, tmp_path):
        secrets = tmp_path / "secrets.yaml"
        secrets.write_text('signup_password: "Vissu1234%"\n', encoding="utf-8")
        assert signup_password(env={}, secrets_path=secrets) == "Vissu1234%"

    def test_password_none_when_unset(self, tmp_path):
        assert signup_password(env={}, secrets_path=tmp_path / "missing.yaml") is None

    def test_email_from_secrets(self, tmp_path):
        secrets = tmp_path / "secrets.yaml"
        secrets.write_text("signup_email: a@b.com\n", encoding="utf-8")
        assert signup_email(env={}, secrets_path=secrets) == "a@b.com"

    def test_email_env_override(self, tmp_path):
        assert signup_email(env={"APPLY_SIGNUP_EMAIL": "x@y.com"}, secrets_path=tmp_path / "n") == (
            "x@y.com"
        )


# --- page-level detection / fill ----------------------------------------

SIGNIN_HTML = """data:text/html,
<h2>Career Opportunities: Sign In</h2>
<form>
  <label for="e">Email Address:*</label><input id="e" type="text">
  <label for="p">Password:*</label><input id="p" type="password">
  <button type="submit">Sign In</button>
</form>
<p>Not a registered user yet? <a href="/register">Create an account</a></p>
"""

FORM_NO_PASSWORD_HTML = """data:text/html,
<form>
  <label for="fn">First Name</label><input id="fn">
  <input aria-label="Email" type="email">
  <button type="submit">Submit application</button>
</form>
"""


class TestWallDetection:
    def test_password_field_flags_a_wall(self, page):
        page.goto(SIGNIN_HTML)
        assert page_has_password_field(page) is True

    def test_plain_form_is_not_a_wall(self, page):
        page.goto(FORM_NO_PASSWORD_HTML)
        assert page_has_password_field(page) is False


class TestControlFinders:
    def test_finds_create_account_link(self, page):
        page.goto(SIGNIN_HTML)
        control = find_register_control(page)
        assert control is not None
        assert "create an account" in (control.text_content() or "").casefold()

    def test_finds_signin_button_not_link(self, page):
        page.goto(SIGNIN_HTML)
        control = find_signin_control(page)
        assert control is not None
        tag = control.evaluate("el => el.tagName.toLowerCase()")
        assert tag == "button"
        assert "sign in" in (control.text_content() or "").casefold()

    def test_no_register_control_on_plain_form(self, page):
        page.goto(FORM_NO_PASSWORD_HTML)
        assert find_register_control(page) is None


class TestFieldFilling:
    def test_fills_email_and_username_fields(self, page):
        page.goto(SIGNIN_HTML)
        assert fill_email_fields(page, "me@example.com") == 1
        assert page.locator("#e").input_value() == "me@example.com"

    def test_sets_all_password_fields(self, page):
        page.goto(
            "data:text/html,<form>"
            "<input type='password' aria-label='Password'>"
            "<input type='password' aria-label='Confirm password'>"
            "</form>"
        )
        assert set_password_fields(page, "Vissu1234%") == 2
        values = page.locator("input[type=password]").evaluate_all("els => els.map(e => e.value)")
        assert values == ["Vissu1234%", "Vissu1234%"]

    def test_password_never_set_when_no_field(self, page):
        page.goto(FORM_NO_PASSWORD_HTML)
        assert set_password_fields(page, "secret") == 0
