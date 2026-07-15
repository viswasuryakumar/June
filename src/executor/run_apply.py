"""Interactive CLI: drive one real application from JobRight's Apply
click through form filling, pausing for the human where required.

Usage (headed, from the repo root):

    python -m src.executor.run_apply --job-url https://jobright.ai/jobs/info/<id>
    python -m src.executor.run_apply --auto          # demo: first feed job

Flow (user-approved 2026-07-14):

1. Launch the persistent logged-in profile *with the JobRight extension
   loaded* (auto-located from the local Chrome install).
2. Open the job detail page (in --auto mode with no --job-url, pick the
   first job on the live feed), click Apply, capture the tab it opens.
3. Route by destination (src/executor/routing.py):
   - LinkedIn sign-in wall: the human types their credentials in the
     headed window (never automated), then filling continues;
   - external ATS: try the JobRight extension's autofill first, then
     audit/verify and fill every gap from the application-data store;
   - extension absent/inert: fill natively from the store.
4. On every page: fill, advance to the next page, and fix any
   validation errors (unfilled required fields) before moving on.
5. Already-filled values are recorded into config/application_data.yaml
   as they are seen, so the store grows with every application.
6. The final Submit is never clicked without an explicit per-job "yes"
   typed at the prompt; --auto mode never clicks it at all (spec S6.2.6).

--auto exists so the run can be driven by a supervisor process with no
usable stdin: keyboard pauses become polling waits (the human still acts
in the headed browser window), and per-stage screenshots are written to
runs/apply-demo/<run_id>/ as reviewable evidence.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from src.auth.context import BrowserContextConfig, is_logged_in, launch_persistent_context
from src.executor.application_data import ApplicationDataStore
from src.executor.extension import find_installed_jobright_extension, trigger_extension_autofill
from src.executor.forms import (
    audit_form_fields,
    fill_missing_fields,
    find_advance_button,
    run_fill_loop,
)
from src.executor.routing import classify_apply_destination
from src.executor.signup import (
    fill_email_fields,
    find_register_control,
    find_signin_control,
    page_has_password_field,
    set_password_fields,
    signup_email,
    signup_password,
)
from src.observability.human import human_settle
from src.observability.run_id import new_run_id
from src.observability.selectors import SelectorRegistry

DEFAULT_SELECTOR_PATH = Path("selectors/jobright.yaml")
APPLY_POPUP_TIMEOUT_MS = 10_000
AUTO_LOGIN_WAIT_S = 240  # --auto: how long the human gets to complete a login wall
SCREENSHOT_ROOT = Path("runs") / "apply-demo"


def _confirm_final_submit() -> bool:
    answer = input(
        "\nReached the final Submit. Review the page in the browser.\n"
        "Type 'yes' to let the automation click Submit, anything else to stop here: "
    )
    return answer.strip().casefold() == "yes"


def _await_human_login(page, *, auto: bool) -> None:
    print(f"\nThis job routed to a sign-in wall: {page.url}")
    if not auto:
        input(
            "Type your credentials in the browser window (the automation never "
            "handles them), finish any 2FA, then press Enter here to continue..."
        )
        return
    print(
        "AUTO MODE: type your credentials in the browser window now - "
        f"polling up to {AUTO_LOGIN_WAIT_S}s for the sign-in wall to clear...",
        flush=True,
    )
    deadline = time.monotonic() + AUTO_LOGIN_WAIT_S
    while time.monotonic() < deadline:
        page.wait_for_timeout(5000)
        if classify_apply_destination(page).kind != "linkedin_login":
            print("Sign-in wall cleared - continuing.")
            return
    print("Sign-in wall still present after the wait - continuing anyway to show the state.")


def _feed_job_url(page, registry, *, index: int = 0) -> str:
    """Return the Nth distinct job detail URL from the feed (page already on
    the feed). Cards can carry two links each, so hrefs are de-duped by job
    id, preserving feed order - `index` then selects among distinct jobs so
    a run can target a job other than the first (e.g. one already applied)."""
    links = page.locator(registry.get("jobs.card_link"))
    # only hrefs matter; the first anchor per card can be a hidden logo
    # wrapper, so "attached" (not "visible") is the right wait state.
    links.first.wait_for(state="attached", timeout=20_000)
    hrefs = links.evaluate_all("els => els.map(e => e.getAttribute('href')).filter(Boolean)")
    seen: list[str] = []
    for href in hrefs:
        if "/jobs/info/" not in href:
            continue
        full = href if href.startswith("http") else "https://jobright.ai" + href
        if full not in seen:
            seen.append(full)
    if not seen:
        raise RuntimeError("No job cards found on the feed")
    return seen[index if 0 <= index < len(seen) else 0]


def _tailor_resume(page, registry, shots: _Snapshotter) -> bool:
    """Drive JobRight's own resume-tailoring drawer before applying
    (flow live-verified 2026-07-13, see PROGRESS.md). Idempotent: a job
    whose resume was already generated shows the review step instead of
    regenerating. Best-effort - a tailoring failure is reported and the
    apply continues with the current resume (spec T5.3 fallback).

    Returns ``(drawer_offers_apply, resume_path)``: the first is True when
    the drawer ends up showing its own APPLY NOW (a fresh generation) - the
    caller should click that directly - and False when it only offers
    Download/Regenerate (an existing resume), which is X-closed here so the
    caller uses the header auto-apply button (user-directed flow,
    2026-07-15). The second is the downloaded tailored-resume file (or
    None), later uploaded into any CV/resume field the extension leaves
    empty.
    """
    drawer_offers_apply = False
    resume_path = None
    try:
        page.locator(registry.get("resume.tailor_button")).first.click(timeout=10_000)
        page.wait_for_timeout(2000)

        improve = page.locator("text=Improve My Resume for This Job")
        if improve.count() and improve.first.is_visible():
            improve.first.click()
            page.wait_for_timeout(2000)
            # survey nudge modal silently blocks all clicks until closed
            close_btn = page.locator(registry.get("resume.survey_nudge_close_button"))
            if close_btn.count() and close_btn.first.is_visible():
                close_btn.first.click()
            select_all = page.locator("text=Select all")
            if select_all.count() and select_all.first.is_visible():
                select_all.first.click()
            generate = page.locator("text=Generate My New Resume")
            if generate.count() and generate.first.is_visible():
                print("Generating the tailored resume (can take a minute)...")
                generate.first.click()
        else:
            print("Tailored resume already generated for this job - not regenerating.")

        # generation done when the drawer shows its review-step controls
        page.locator("text=/Download Resume|APPLY NOW/i").first.wait_for(
            state="visible", timeout=120_000
        )
        print("Tailored resume ready.")
        shots.take(page, "resume-tailored")
        resume_path = _download_tailored_resume(page, shots)
        apply_now = page.locator("text=/APPLY NOW/i").first
        drawer_offers_apply = bool(apply_now.count() and apply_now.is_visible())
    except Exception as exc:
        print(
            f"Resume tailoring step did not complete ({exc.__class__.__name__}) - "
            "continuing with the current resume."
        )
    if not drawer_offers_apply:
        # existing-resume view (Download/Regenerate only): X-close the
        # drawer, then the caller clicks the header auto-apply button.
        # Never press Escape - that closes the entire job overlay back to
        # the feed (live-verified).
        try:
            close_x = page.locator(registry.get("resume.drawer_close_button")).first
            if close_x.count() and close_x.is_visible():
                close_x.click()
                page.wait_for_timeout(1000)
                print("Closed the resume drawer via its X.")
            else:
                print("Drawer X close button not found - drawer may still be open.")
        except Exception:
            pass
    else:
        print("Drawer offers APPLY NOW directly (fresh generation) - will click it.")
    return drawer_offers_apply, resume_path


def _download_tailored_resume(page, shots: _Snapshotter):
    """Download JobRight's tailored resume as a PDF from the drawer's review
    step and save it under the run dir, so it can be uploaded into a
    CV/resume field the extension leaves empty. "Download Resume" opens a
    format menu, so this always picks the PDF option (user-directed: PDF
    always). Best-effort: returns the saved Path or None."""
    try:
        button = page.locator("text=/Download Resume/i").first
        if not (button.count() and button.is_visible()):
            return None
        with page.expect_download(timeout=30_000) as download_info:
            button.click()
            # a format menu (PDF / Word) usually pops up - always take PDF.
            page.wait_for_timeout(1200)
            pdf_option = page.locator("text=/\\bPDF\\b/i").first
            try:
                if pdf_option.count() and pdf_option.is_visible():
                    print("Choosing the PDF download format...")
                    pdf_option.click()
            except Exception:
                pass
        download = download_info.value
        shots.dir.mkdir(parents=True, exist_ok=True)
        # always save with a .pdf extension (user-directed: PDF always).
        path = shots.dir / "tailored-resume.pdf"
        download.save_as(str(path))
        print(f"Downloaded the tailored resume (PDF) -> {path}")
        return path
    except Exception as exc:
        print(f"Could not download the tailored resume ({exc.__class__.__name__}) - skipping.")
        return None


def _close_duplicate_tabs(context, keep_page) -> int:
    """JobRight can open a second copy of the application site moments
    after the first (live-observed 2026-07-15) - close every non-JobRight
    tab except `keep_page`. Returns how many were closed."""
    closed = 0
    for open_page in list(context.pages):
        if open_page is keep_page:
            continue
        try:
            url = open_page.url
            if url and not url.startswith("about:") and "jobright.ai" not in url:
                open_page.close()
                closed += 1
        except Exception:
            continue
    if closed:
        print(f"Closed {closed} duplicate application tab(s).")
    return closed


# Company careers pages usually land on a job posting / landing page whose
# "Apply now" affordance is a plain <a> link (not a <button>), so the fill
# loop's find_advance_button never sees it. These drill through to the real
# form (user-directed 2026-07-15: "it should click Apply now / any apply
# button on the company website").
_APPLY_LINK_PHRASES = (
    "apply now",
    "apply for this job",
    "apply for this position",
    "apply online",
    "apply today",
    "start application",
    "start your application",
    "apply",
)
_COOKIE_ACCEPT_PHRASES = (
    "accept all cookies",
    "accept all",
    "accept cookies",
    "accept",
    "allow all",
    "i agree",
    "agree",
)


def _dismiss_cookie_banner(page) -> bool:
    """Click an Accept/Agree consent button if one is up - cookie banners
    overlay the page and intercept the very Apply click we need next."""
    try:
        elements = page.locator("button, a, [role=button]").all()
    except Exception:
        return False
    for element in elements:
        try:
            if not element.is_visible():
                continue
            text = " ".join((element.text_content() or "").split()).casefold()
        except Exception:
            continue
        if not text or len(text) > 30:
            continue
        if any(text == p or text.startswith(p) for p in _COOKIE_ACCEPT_PHRASES):
            try:
                element.click(timeout=3000)
                page.wait_for_timeout(800)
                print(f"Dismissed a consent banner ({text!r}).")
                return True
            except Exception:
                continue
    return False


def _looks_like_application_form(page) -> bool:
    """True when the page is an actual data-entry form (has an email field
    or several text inputs), not a job posting / search-and-filter page
    (whose only controls are filter <select>s)."""
    try:
        audits = audit_form_fields(page)
    except Exception:
        return False
    if any(a.kind == "email" for a in audits):
        return True
    text_like = [a for a in audits if a.kind in ("text", "tel", "textarea")]
    return len(text_like) >= 3


def _find_company_apply_control(page):
    """The company page's own Apply link/button (anchors included), or None
    when none is visible. Skipped once we're already on a form so we never
    mistake a form's 'Apply now' submit button for a navigation link."""
    try:
        elements = page.locator("a, button, input[type=submit], [role=button]").all()
    except Exception:
        return None
    for element in elements:
        try:
            if not element.is_visible() or element.is_disabled():
                continue
            text = (element.text_content() or "").strip()
            if not text:
                text = element.evaluate("el => el.value || el.getAttribute('aria-label') || ''")
            text = " ".join((text or "").split()).casefold()
        except Exception:
            continue
        if not text or len(text) > 40:
            continue
        if any(text == p or text.startswith(p) for p in _APPLY_LINK_PHRASES):
            return element
    return None


def _open_company_application_form(page, context, shots: _Snapshotter, *, max_hops: int = 3):
    """Click through company Apply links until a real form appears.

    A careers page often opens on a posting/landing page whose "Apply now"
    is a link to the actual application form (possibly in a new tab). Follow
    up to `max_hops` of those, stopping as soon as the page looks like a
    data-entry form (so a form's own Apply/Submit button is never clicked).
    Returns the page to keep working on (a new tab if Apply opened one).
    """
    for hop in range(1, max_hops + 1):
        if _looks_like_application_form(page):
            return page
        control = _find_company_apply_control(page)
        if control is None:
            return page
        try:
            label = " ".join((control.text_content() or "").split())[:40] or "apply"
        except Exception:
            label = "apply"
        print(f"No form here yet - clicking the company apply control ({label!r})...")
        before_url = page.url
        try:
            with context.expect_page(timeout=6000) as new_info:
                control.click(timeout=8000)
            page = new_info.value
            page.wait_for_load_state("domcontentloaded")
            page.bring_to_front()
            print(f"Apply opened a new tab: {page.url}")
        except Exception:
            # no new tab caught - it likely navigated in place
            try:
                if page.url == before_url:
                    control.click(timeout=8000)
            except Exception:
                pass
            try:
                page.wait_for_load_state("domcontentloaded")
            except Exception:
                pass
        page.wait_for_timeout(2500)
        _dismiss_cookie_banner(page)
        shots.take(page, f"company-apply-hop-{hop}")
    return page


CAPTCHA_HOLD_S = 300  # how long a human gets to solve a captcha (spec S6.2.5)

_CAPTCHA_IFRAME_SELECTOR = (
    "iframe[src*='hcaptcha'], iframe[src*='recaptcha'], iframe[src*='turnstile']"
)


def _captcha_visible(page) -> bool:
    try:
        for frame_el in page.locator(_CAPTCHA_IFRAME_SELECTOR).all():
            box = frame_el.bounding_box()
            # challenge iframes are large; the invisible checkbox/anchor
            # frames that always exist on the page are small or hidden.
            if frame_el.is_visible() and box and box["width"] > 250 and box["height"] > 250:
                return True
    except Exception:
        pass
    return False


def _hold_for_captcha(page, shots: _Snapshotter) -> bool:
    """Never solve a captcha - hold the page open for the human instead
    (spec S6.2.5), continuing as soon as the challenge disappears. Returns
    True if a captcha was seen (and presumably solved by the human)."""
    if not _captcha_visible(page):
        return False
    shots.take(page, "captcha-held-for-human")
    print(
        f"\nCAPTCHA detected - solve it in the browser window now. "
        f"Holding the page for up to {CAPTCHA_HOLD_S}s..."
    )
    deadline = time.monotonic() + CAPTCHA_HOLD_S
    while time.monotonic() < deadline:
        page.wait_for_timeout(3000)
        if not _captcha_visible(page):
            print("Captcha cleared - continuing.")
            page.wait_for_timeout(3000)
            return True
    print("Captcha still present after the hold window - continuing to wrap up.")
    return True


def _still_on_apply_form(page) -> bool:
    from src.executor.routing import classify_apply_destination

    try:
        return classify_apply_destination(page).kind == "external_ats"
    except Exception:
        return False


def _finish_submission_with_captchas(page, shots: _Snapshotter, *, max_rounds: int = 5) -> None:
    """Drive the Submit/captcha handshake to completion.

    hCaptcha (live-verified on Lever 2026-07-15) issues a FRESH challenge
    on every Submit press, so this alternates: hold for the human to solve
    the captcha, then re-click Submit, and repeat until the page leaves
    the application form (success) or the round budget is exhausted. The
    captcha itself is never solved by the automation (spec S6.2.5)."""
    for round_no in range(1, max_rounds + 1):
        saw_captcha = _hold_for_captcha(page, shots)
        if not _still_on_apply_form(page):
            return  # navigated away -> submitted
        button = find_advance_button(page)
        if button is None or not button.is_final_submit:
            # no submit button left and still on the form: nothing more we
            # can safely click (may be a non-captcha validation state).
            if not saw_captcha:
                return
            continue
        print(f"Clicking Submit (round {round_no})...")
        try:
            button.locator.click(timeout=8000)
        except Exception as exc:
            print(f"  Submit click failed ({exc.__class__.__name__}).")
            return
        page.wait_for_timeout(4000)
        shots.take(page, f"after-submit-round-{round_no}")
        if not _still_on_apply_form(page):
            return  # submitted


def _report_submission_outcome(page, shots: _Snapshotter) -> None:
    url = page.url
    confirmed = "thanks" in url or "confirmation" in url
    if not confirmed:
        try:
            confirmed = page.locator(
                "text=/application (has been )?submitted|thank you for applying/i"
            ).first.is_visible(timeout=3000)
        except Exception:
            confirmed = False
    shots.take(page, "submission-outcome")
    if confirmed:
        print(f"SUBMISSION CONFIRMED - success signal found at {url}")
    else:
        print(
            f"Submit was clicked but no success signal is visible yet at {url} - "
            "verify in the browser window before treating this as submitted."
        )


EMAIL_VERIFY_WAIT_S = 300  # --auto: how long to hold for the human to verify


def _click_and_adopt(page, context, control):
    """Click a control that may navigate in place or open a new tab, and
    return the page to keep working on (the new tab if one opened)."""
    before_url = page.url
    try:
        with context.expect_page(timeout=6000) as new_info:
            control.click(timeout=8000)
        new_page = new_info.value
        try:
            new_page.wait_for_load_state("domcontentloaded")
            new_page.bring_to_front()
        except Exception:
            pass
        return new_page
    except Exception:
        # no new tab caught - the click likely navigated in place; only
        # re-click if nothing moved, so we never double-submit.
        try:
            if page.url == before_url:
                control.click(timeout=8000)
        except Exception:
            pass
        try:
            page.wait_for_load_state("domcontentloaded")
        except Exception:
            pass
        return page


def _await_email_verification(page, *, auto: bool) -> None:
    print(
        "\nRegistration submitted. A verification email was likely sent to your "
        "inbox - open its link to activate the account."
    )
    if not auto:
        input("Press Enter here once you've clicked the verification link...")
        return
    print(
        f"AUTO MODE: open the verification link from your email now - "
        f"holding up to {EMAIL_VERIFY_WAIT_S}s...",
        flush=True,
    )
    deadline = time.monotonic() + EMAIL_VERIFY_WAIT_S
    start_url = page.url
    while time.monotonic() < deadline:
        page.wait_for_timeout(5000)
        if page.url != start_url or not page_has_password_field(page):
            print("Page changed after verification - continuing.")
            return
    print("Verification hold elapsed - continuing to show the current state.")


def _handle_account_wall(page, context, store, shots: _Snapshotter, *, auto: bool):
    """Company-ATS account wall: try the saved credentials first, and only
    create an account when sign-in shows it does not exist (user-directed
    2026-07-15). Returns the page to keep working on. Passwords are only
    ever the user-provided one; captchas are held for the human, and the
    email-verification link is left for the human by design."""
    password = signup_password()
    email = signup_email()
    if not password:
        print(
            "No signup password configured (set config/secrets.yaml) - "
            "pausing for you to sign in manually."
        )
        _await_human_login(page, auto=auto)
        return page

    print("Company ATS wants an account - trying your saved credentials first...")
    _dismiss_cookie_banner(page)
    if email:
        fill_email_fields(page, email)
    else:
        fill_missing_fields(audit_form_fields(page), store)
    set_password_fields(page, password)
    shots.take(page, "signin-filled")
    signin = find_signin_control(page)
    if signin is not None:
        page = _click_and_adopt(page, context, signin)
        _hold_for_captcha(page, shots)
        page.wait_for_timeout(3000)

    if not page_has_password_field(page):
        print("Signed in with the saved credentials.")
        shots.take(page, "signed-in")
        return page

    print("Sign-in did not go through (account may not exist) - creating an account...")
    register = find_register_control(page)
    if register is None:
        print("No 'Create an account' control found - pausing for you to sign in manually.")
        _await_human_login(page, auto=auto)
        return page
    page = _click_and_adopt(page, context, register)
    _dismiss_cookie_banner(page)
    page.wait_for_timeout(1500)

    fill_missing_fields(audit_form_fields(page), store)
    if email:
        fill_email_fields(page, email)
    set_password_fields(page, password)
    shots.take(page, "signup-filled")
    _hold_for_captcha(page, shots)

    submit = find_register_control(page)
    if submit is not None:
        print("Submitting the new-account registration...")
        page = _click_and_adopt(page, context, submit)
        page.wait_for_timeout(3000)
    shots.take(page, "signup-submitted")
    _await_email_verification(page, auto=auto)
    return page


def _click_apply(page, context, registry, *, drawer_offers_apply: bool = False):
    """Click whichever apply affordance this job carries, returning the
    new tab it opens (or None).

    Live-verified button variants (2026-07-14/15): `apply.agent_button`
    ("APPLY NOW", also duplicated inside the tailoring drawer's review
    step) and `apply.extension_autofill_button` ("APPLY WITH AUTOFILL",
    extension-routed jobs). A first click can merely close the tailoring
    drawer and reveal the real header button, so each candidate gets two
    passes before giving up.
    """

    def external_tab():
        # the popup listener can miss slow-opening tabs (live-observed
        # 2026-07-15: the APPLY WITH AUTOFILL tab appeared *after* the
        # 10s expect_page window) - so also scan every open tab for a
        # non-JobRight page and adopt the newest one.
        for open_page in reversed(context.pages):
            url = open_page.url
            if url and not url.startswith("about:") and "jobright.ai" not in url:
                return open_page
        return None

    def adopt(new_page, how: str):
        try:
            new_page.wait_for_load_state("domcontentloaded")
            new_page.bring_to_front()
        except Exception:
            pass
        # single-tab hygiene: if more than one external tab opened (e.g.
        # a double-fired click), keep the adopted one and close the rest.
        for open_page in context.pages:
            if open_page is new_page:
                continue
            url = open_page.url
            if url and not url.startswith("about:") and "jobright.ai" not in url:
                try:
                    open_page.close()
                except Exception:
                    pass
        print(f"Apply opened the application tab ({how}): {new_page.url}")
        return new_page

    # candidate order: the drawer's own APPLY NOW when a fresh generation
    # left it open (user-directed), then the header auto-apply (extension
    # path, the user's preferred route), then the plain APPLY NOW header.
    candidates = []
    if drawer_offers_apply:
        candidates.append(("drawer APPLY NOW", "text=/APPLY NOW/i"))
    candidates.append(
        ("header APPLY WITH AUTOFILL", registry.get("apply.extension_autofill_button"))
    )
    candidates.append(("header APPLY NOW", registry.get("apply.agent_button")))

    for _attempt in range(2):
        for name, selector in candidates:
            existing = external_tab()
            if existing is not None:
                return adopt(existing, "already open")
            locator = page.locator(selector).last
            try:
                if not locator.count() or not locator.is_visible():
                    continue
            except Exception:
                continue
            print(f"  trying {name}...")
            try:
                with context.expect_page(timeout=APPLY_POPUP_TIMEOUT_MS) as new_page_info:
                    locator.click(timeout=8000)
                return adopt(new_page_info.value, f"via {name}")
            except Exception as exc:
                # no tab caught within the window - it may still be
                # opening; give it a moment before clicking anything else,
                # so one apply never spawns two tabs.
                print(f"  {name}: no new tab yet ({exc.__class__.__name__})")
                page.wait_for_timeout(5000)
                late = external_tab()
                if late is not None:
                    return adopt(late, f"late, via {name}")
        page.wait_for_timeout(3000)
    late = external_tab()
    return adopt(late, "late") if late is not None else None


class _Snapshotter:
    def __init__(self, run_id: str):
        self.dir = SCREENSHOT_ROOT / run_id
        self.counter = 0

    def take(self, page, name: str) -> None:
        self.counter += 1
        self.dir.mkdir(parents=True, exist_ok=True)
        path = self.dir / f"{self.counter:02d}-{name}.png"
        try:
            page.screenshot(path=str(path), full_page=False)
            print(f"  [screenshot] {path}")
        except Exception as exc:
            print(f"  [screenshot failed: {exc}]")


def run_apply(
    job_url: str | None,
    *,
    max_steps: int = 10,
    auto: bool = False,
    submit: bool = False,
    feed_index: int = 0,
) -> int:
    run_id = new_run_id()
    registry = SelectorRegistry.load(DEFAULT_SELECTOR_PATH)
    store = ApplicationDataStore.load()
    shots = _Snapshotter(run_id)

    extension_path = find_installed_jobright_extension()
    if extension_path is not None:
        print(f"Loading JobRight extension from: {extension_path}")
    else:
        print("JobRight extension not found locally - continuing without it.")

    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        context = launch_persistent_context(
            BrowserContextConfig(headless=False, extension_path=extension_path), p
        )
        # reuse the initial blank tab instead of opening a second one
        page = context.pages[0] if context.pages else context.new_page()

        # the login indicator (header profile link) only renders on the
        # feed, not on job detail pages - so always check login there. The
        # feed can render its (personalized, auth-only) job cards before
        # the header link, so either signal counts; the slow feed also
        # needs a longer wait than is_logged_in's default.
        page.goto(registry.get("jobs.feed_url"), wait_until="domcontentloaded")
        # Human-like settle: wait 0-16s with mouse crawling the page
        # before any interaction, so behavioral analytics see real
        # mouse activity and non-deterministic timing (spec §6).
        print("Settling in (human-like browsing)...")
        human_settle(page, run_id=run_id)
        try:
            page.locator(
                f"{registry.get('login.dashboard_indicator')}, {registry.get('jobs.card')}"
            ).first.wait_for(state="visible", timeout=30_000)
            logged_in = True
        except Exception:
            logged_in = is_logged_in(page, registry, run_id, timeout_ms=5_000)
        if not logged_in:
            print("Not logged in to JobRight - run `python -m src.auth.run_login` first.")
            shots.take(page, "not-logged-in")
            context.close()
            return 1

        if job_url is None:
            print(f"No --job-url given - picking feed job #{feed_index} on the live feed...")
            job_url = _feed_job_url(page, registry, index=feed_index)
            print(f"Picked: {job_url}")

        job_id = job_url.rstrip("/").split("/")[-1].split("?")[0]
        page.goto(job_url, wait_until="domcontentloaded")

        # Human-like settle on the job detail page before tailoring.
        print("Settling in (human-like browsing)...")
        human_settle(page, run_id=run_id)

        # the freshly-loaded extension can open its own JobRight tab on
        # startup; close every tab except ours so the run stays single-tab.
        for open_page in list(context.pages):
            if open_page is not page:
                try:
                    open_page.close()
                except Exception:
                    pass
        shots.take(page, "job-detail-page")

        print("Tailoring the resume for this job first...")
        drawer_offers_apply, resume_path = _tailor_resume(page, registry, shots)

        print("Clicking Apply...")
        apply_page = _click_apply(page, context, registry, drawer_offers_apply=drawer_offers_apply)
        if apply_page is None:
            apply_page = page
            print("Apply did not open a new tab - continuing on the current page.")

        destination = classify_apply_destination(apply_page)
        print(f"Destination: {destination.kind} ({destination.url})")
        shots.take(apply_page, f"destination-{destination.kind}")

        # Human-like settle on the external application page before
        # triggering autofill or filling forms.
        print("Settling in (human-like browsing)...")
        human_settle(apply_page, run_id=run_id)

        if destination.kind == "linkedin_login":
            _await_human_login(apply_page, auto=auto)
            destination = classify_apply_destination(apply_page)
            shots.take(apply_page, f"after-login-{destination.kind}")

        if apply_page is not page:
            _close_duplicate_tabs(context, apply_page)

        if destination.kind == "jobright":
            # never run the generic form filler against JobRight's own UI -
            # its sidebar widgets are not an application form (learned the
            # hard way on 2026-07-15: it filled the "Find Any Email" box).
            print(
                "Still on JobRight after the Apply click - no external application "
                "form opened, so there is nothing to fill. Stopping this job here."
            )
        else:
            # A company careers page often opens on a posting/landing page
            # whose "Apply now" is a link to the real form - click through
            # to it before autofilling (user-directed 2026-07-15).
            if destination.kind in ("external_ats", "unknown"):
                _dismiss_cookie_banner(apply_page)
                drilled = _open_company_application_form(apply_page, context, shots)
                if drilled is not apply_page:
                    apply_page = drilled
                    _close_duplicate_tabs(context, apply_page)
                    destination = classify_apply_destination(apply_page)
                    print(f"On application form: {destination.kind} ({destination.url})")
                    shots.take(apply_page, f"form-{destination.kind}")

                # Account wall (SuccessFactors/Workday/...): sign in with the
                # saved credentials, creating an account if none exists, then
                # drill on to the real application form.
                if page_has_password_field(apply_page):
                    walled = _handle_account_wall(apply_page, context, store, shots, auto=auto)
                    if walled is not apply_page:
                        apply_page = walled
                        _close_duplicate_tabs(context, apply_page)
                    _dismiss_cookie_banner(apply_page)
                    apply_page = _open_company_application_form(apply_page, context, shots)
                    destination = classify_apply_destination(apply_page)
                    print(f"After sign-in, on: {destination.kind} ({destination.url})")
                    shots.take(apply_page, f"post-auth-{destination.kind}")

            if apply_page.is_closed():
                print(
                    "The application tab closed itself before filling (a one-click-apply "
                    "flow or a bot block) - nothing to fill on this job."
                )
            elif destination.kind == "external_ats":
                attempt = trigger_extension_autofill(apply_page, registry=registry, run_id=run_id)
                if attempt.triggered:
                    print(
                        f"Extension autofill triggered via {attempt.detail} - "
                        f"{attempt.changed_fields} fields changed. Verifying + filling gaps..."
                    )
                else:
                    print(
                        "Extension autofill unavailable here - filling natively from stored data."
                    )

            result = run_fill_loop(
                apply_page,
                store,
                job_id=job_id,
                ats=destination.ats,
                run_id=run_id,
                max_steps=max_steps,
                # --submit = the user pre-approved this specific job's
                # submission; otherwise --auto never submits and
                # interactive mode asks at the prompt (spec S6.2.6).
                confirm_final_submit=(
                    (lambda: True) if submit else (None if auto else _confirm_final_submit)
                ),
                resume_path=resume_path,
            )
            shots.take(apply_page, f"after-fill-{result.status}")

            if result.status == "submitted_click":
                _close_duplicate_tabs(context, apply_page)
                _finish_submission_with_captchas(apply_page, shots)
                _report_submission_outcome(apply_page, shots)

            print(f"\nFill loop finished: {result.status} at {result.final_url}")
            for step in result.steps:
                print(
                    f"  page {step.step}: filled {len(step.filled)}, "
                    f"unresolved required {len(step.unresolved_required)}"
                    + (f", errors: {step.validation_errors}" if step.validation_errors else "")
                )
                for label, answer in step.filled:
                    print(f"    filled {label!r} -> {answer!r}")
                for label in step.unresolved_required:
                    print(f"    needs a stored answer: {label!r}")
        store.save()
        print(f"Application data store updated: {store.store_path}")
        print(f"Screenshots: {shots.dir}")

        if auto:
            print("AUTO MODE: leaving the browser open 60s for inspection, then closing.")
            page.wait_for_timeout(60_000)
        else:
            input("\nPress Enter to close the browser...")
        context.close()
    return 0


def main() -> int:
    # Windows consoles default to cp1252, which cannot print arbitrary
    # page text (field labels, button captions) this script echoes back.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--job-url",
        default=None,
        help="jobright.ai job detail URL (default in --auto: first feed job)",
    )
    parser.add_argument("--max-steps", type=int, default=10)
    parser.add_argument(
        "--feed-index",
        type=int,
        default=0,
        help="which distinct feed job to pick when no --job-url is given (0 = first)",
    )
    parser.add_argument(
        "--auto",
        action="store_true",
        help="no-stdin demo mode: poll instead of prompting, never click final Submit",
    )
    parser.add_argument(
        "--submit",
        action="store_true",
        help="click the final Submit without prompting - only pass this when the "
        "user has explicitly approved submitting this specific application",
    )
    args = parser.parse_args()
    if args.job_url is None and not args.auto:
        parser.error("--job-url is required unless --auto is given")
    return run_apply(
        args.job_url,
        max_steps=args.max_steps,
        auto=args.auto,
        submit=args.submit,
        feed_index=args.feed_index,
    )


if __name__ == "__main__":
    raise SystemExit(main())
