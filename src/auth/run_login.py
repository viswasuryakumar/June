"""CLI entrypoint: launch a headed browser, click through JobRight's
sign-in modal and Google SSO button, then hand off to a human for the
actual Google credential entry.

Verified against the live https://jobright.ai site on 2026-07-13:
clicking the nav "SIGN IN" text opens an in-page modal; the modal's
"Sign in with Google" control is a cross-origin Google Identity Services
iframe (`selectors/jobright.yaml`'s `login.sso_google_button`) - clicking
it opens a real accounts.google.com OAuth popup window. This script
drives both of those clicks and then stops: it never fills or submits
anything in the Google popup, since Google actively blocks automated
sign-in and this is meant to be a human-completed step.
"""

from __future__ import annotations

from pathlib import Path

from src.auth.context import (
    BrowserContextConfig,
    is_logged_in,
    launch_persistent_context,
    persist_storage_state,
)
from src.observability.run_id import new_run_id
from src.observability.selectors import SelectorRegistry, resolve_locator

DEFAULT_SELECTOR_PATH = Path("selectors/jobright.yaml")
JOBRIGHT_URL = "https://jobright.ai"
POPUP_TIMEOUT_MS = 15_000


def build_registry() -> SelectorRegistry:
    return SelectorRegistry.load(DEFAULT_SELECTOR_PATH)


def main() -> int:
    run_id = new_run_id()
    registry = build_registry()

    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        context = launch_persistent_context(BrowserContextConfig(headless=False), p)
        page = context.new_page()
        page.goto(JOBRIGHT_URL, wait_until="domcontentloaded")

        print("Opening the JobRight sign-in modal...")
        resolve_locator(
            page, registry, "login.sign_in_button", run_id=run_id, timeout_ms=15_000
        ).click()

        print("Clicking 'Sign in with Google'...")
        google_button = resolve_locator(
            page, registry, "login.sso_google_button", run_id=run_id, timeout_ms=15_000
        )
        # The Google Identity Services iframe can be visible before its
        # internal click handler has attached; a short settle avoids a
        # race where the click lands before the popup listener is ready
        # (observed empirically - see coordination/claims/auth-playwright-login--copilot.md).
        page.wait_for_timeout(1000)

        popup = None
        try:
            with context.expect_page(timeout=POPUP_TIMEOUT_MS) as popup_info:
                google_button.click()
            popup = popup_info.value
            popup.wait_for_load_state("domcontentloaded")
            popup.bring_to_front()
            print(f"A Google sign-in window opened: {popup.url}")
        except Exception:
            print("Clicked the Google button, but no separate sign-in window was detected.")
            print("Check the browser window - Google's sign-in may have opened in the same tab.")

        input(
            "\nEnter your Google email/password (and complete any 2FA) in the opened "
            "window, then press Enter here to continue...\n"
        )

        target_page = popup if (popup is not None and not popup.is_closed()) else page
        try:
            target_page.wait_for_load_state("networkidle", timeout=20_000)
        except Exception:
            pass

        logged_in = is_logged_in(page, registry, run_id, timeout_ms=10_000)
        if logged_in:
            persist_storage_state(context)
            print("Login detected - session saved to the persistent profile.")
        else:
            print(
                "Could not confirm login yet (login.dashboard_indicator hasn't been "
                "verified against a real logged-in account - see selectors/jobright.yaml). "
                "Check the browser window to confirm manually."
            )

        context.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
