"""Company-ATS account creation (authorized self-registration).

Some company ATSes (SAP SuccessFactors, Workday, iCIMS, ...) gate their
application forms behind a user account. This module recognizes such a
sign-in / registration wall, finds the site's own "Create an account"
control, and fills the registration form from the shared application-data
store plus the user-provided signup password — so the real application
form can follow.

Scope limits kept identical to the rest of the executor: the password is
only ever the one the user supplies (env var or a git-ignored secrets
file — never invented); captchas are never solved (spec S6.2.5); and the
email-verification link is left for the human by design. The browser-flow
orchestration (clicking through, submitting, holding for captcha/verify)
lives in run_apply; this module holds the reusable, testable mechanics.
"""

from __future__ import annotations

import os
from pathlib import Path

import yaml

from src.executor.forms import audit_form_fields

SIGNUP_PASSWORD_ENV = "APPLY_SIGNUP_PASSWORD"
SIGNUP_EMAIL_ENV = "APPLY_SIGNUP_EMAIL"
DEFAULT_SECRETS_PATH = Path("config/secrets.yaml")

# Sign-in affordances, so the flow can log in *before* trying to register
# (user-directed: try the saved credentials first, create an account only
# when sign-in shows the account does not exist).
_SIGNIN_PHRASES = ("sign in", "signin", "log in", "login")

# Phrases that identify a "create a new account" affordance. Matched as a
# substring so "Not a registered user yet? Create an account" is caught.
_REGISTER_PHRASES = (
    "create an account",
    "create account",
    "create a new account",
    "register now",
    "register",
    "sign up",
    "signup",
    "new user",
    "join now",
)


def signup_password(*, env=None, secrets_path: Path | str = DEFAULT_SECRETS_PATH) -> str | None:
    """The user-provided signup password, or None when unset.

    Env var ``APPLY_SIGNUP_PASSWORD`` wins; otherwise a git-ignored
    ``config/secrets.yaml`` with a ``signup_password:`` key. Never returns
    a generated value — the caller must not create an account without one.
    """
    env = os.environ if env is None else env
    value = (env.get(SIGNUP_PASSWORD_ENV) or "").strip()
    if value:
        return value
    path = Path(secrets_path)
    if path.exists():
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        stored = str(raw.get("signup_password") or "").strip()
        if stored:
            return stored
    return None


def signup_email(*, env=None, secrets_path: Path | str = DEFAULT_SECRETS_PATH) -> str | None:
    """The login/registration email, or None. Env var
    ``APPLY_SIGNUP_EMAIL`` wins; else ``signup_email:`` in secrets.yaml.
    When None the caller falls back to the profile email in the store."""
    env = os.environ if env is None else env
    value = (env.get(SIGNUP_EMAIL_ENV) or "").strip()
    if value:
        return value
    path = Path(secrets_path)
    if path.exists():
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        stored = str(raw.get("signup_email") or "").strip()
        if stored:
            return stored
    return None


def page_has_password_field(page_or_frame) -> bool:
    """True when the page shows a password input — the tell of a sign-in
    or registration wall rather than a plain application form."""
    try:
        return any(a.kind == "password" for a in audit_form_fields(page_or_frame))
    except Exception:
        return False


def _control_text(element) -> str:
    text = (element.text_content() or "").strip()
    if not text:
        text = element.evaluate("el => el.value || el.getAttribute('aria-label') || ''") or ""
    return " ".join(text.split()).casefold()


def find_register_control(page_or_frame):
    """The visible "Create an account" / register link or button (anchors
    included), or None. Used both to reach the registration form and to
    submit it (its button typically reads "Create Account"/"Register")."""
    try:
        elements = page_or_frame.locator("a, button, input[type=submit], [role=button]").all()
    except Exception:
        return None
    for element in elements:
        try:
            if not element.is_visible() or element.is_disabled():
                continue
            text = _control_text(element)
        except Exception:
            continue
        if not text or len(text) > 40:
            continue
        if any(phrase in text for phrase in _REGISTER_PHRASES):
            return element
    return None


def find_signin_control(page_or_frame):
    """The visible Sign In / Log In button (never a plain 'sign in' link
    in nav), or None. Prefers submit-type controls so we click the login
    form's own button, not a header link."""
    try:
        elements = page_or_frame.locator("button, input[type=submit], [role=button], a").all()
    except Exception:
        return None
    fallback = None
    for element in elements:
        try:
            if not element.is_visible() or element.is_disabled():
                continue
            text = _control_text(element)
            tag = element.evaluate("el => el.tagName.toLowerCase()")
        except Exception:
            continue
        if not text or len(text) > 30:
            continue
        if any(phrase == text or text.startswith(phrase) for phrase in _SIGNIN_PHRASES):
            if tag in ("button", "input"):
                return element
            fallback = fallback or element
    return fallback


def fill_email_fields(page_or_frame, email: str) -> int:
    """Fill the login/registration email (or username) field with `email`.
    Returns how many were set. Targets type=email inputs and any text input
    whose label/name mentions email or username."""
    count = 0
    for audit in audit_form_fields(page_or_frame):
        label = f"{audit.label} {audit.name}".casefold()
        is_email = audit.kind == "email" or (
            audit.kind == "text" and ("email" in label or "username" in label or "user id" in label)
        )
        if not is_email:
            continue
        try:
            audit.locator.fill(email)
            count += 1
        except Exception:
            continue
    return count


def set_password_fields(page_or_frame, password: str) -> int:
    """Fill every visible password input with `password` (covers both the
    password and any confirm-password field). Returns how many were set.
    This is the only place the executor ever writes a password — always
    the user-supplied one, never a stored/learned answer."""
    count = 0
    for audit in audit_form_fields(page_or_frame):
        if audit.kind != "password":
            continue
        try:
            audit.locator.fill(password)
            count += 1
        except Exception:
            continue
    return count
