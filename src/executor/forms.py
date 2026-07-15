"""Generic form audit + fill engine (spec S6.2.3 / S6.2.4 / S6.3.3).

Works on arbitrary application pages (LinkedIn Easy Apply, ATS sites,
company career pages), so field discovery is semantic — label/aria/
placeholder text matched against the application-data store — rather
than per-site registry selectors (those remain jobright.ai-only).

The engine does, per page:

1. ``audit_form_fields``  — walk every visible form control, extract its
   question text, requiredness, and current value.
2. record what is *already filled* (e.g. by the JobRight extension) into
   the application-data store — this is how the store grows while
   applications are being filled.
3. ``fill_missing_fields`` — fill empty fields whose question the store
   can answer; anything unanswerable is reported, never guessed.
4. ``find_validation_errors`` — after attempting to advance, detect
   inline validation failures so missed fields can be fixed and retried.
5. ``run_fill_loop`` — repeat across multi-step forms via next/continue
   buttons, with a hard step cap and a final-submit confirmation gate
   (the loop never clicks a final submit unless its confirmation
   callback explicitly returns True — spec S6.2.6).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher

from src.executor.application_data import ApplicationDataStore, is_third_party_name_question
from src.observability.human import human_fill_locator, human_hover_locator
from src.observability.logging import log_event

# One JS pass per control keeps label extraction identical across ATSes:
# explicit <label for=...>, wrapping <label>, aria-label(ledby),
# placeholder, then name attribute as a last resort.
_DESCRIBE_JS = """
(el) => {
  const clean = (t) => (t || "").replace(/\\s+/g, " ").trim();
  let label = "";
  if (el.id) {
    const forLabel = document.querySelector(`label[for="${CSS.escape(el.id)}"]`);
    if (forLabel) label = clean(forLabel.textContent);
  }
  if (!label) {
    const wrap = el.closest("label");
    if (wrap) label = clean(wrap.textContent);
  }
  if (!label) label = clean(el.getAttribute("aria-label"));
  if (!label && el.getAttribute("aria-labelledby")) {
    label = clean(
      el.getAttribute("aria-labelledby").split(/\\s+/)
        .map((id) => { const n = document.getElementById(id); return n ? n.textContent : ""; })
        .join(" ")
    );
  }
  if (!label) label = clean(el.getAttribute("placeholder"));
  if (!label) label = clean(el.getAttribute("name"));
  const tag = el.tagName.toLowerCase();
  const type = tag === "input" ? (el.getAttribute("type") || "text").toLowerCase() : tag;
  let value = "";
  let checked = null;
  if (type === "checkbox" || type === "radio") {
    checked = el.checked;
    value = el.checked ? "checked" : "";
  } else if (tag === "select") {
    const opt = el.selectedOptions && el.selectedOptions[0];
    value = opt && el.value !== "" ? clean(opt.textContent) : "";
  } else {
    value = el.value || "";
  }
  // radios/checkboxes: their own label is just the option text ("Yes"),
  // the real question lives on the surrounding group (fieldset legend or
  // a question container - e.g. Lever's li.application-question >
  // .application-label). Keep both.
  let optionLabel = "";
  if (type === "radio" || type === "checkbox") {
    const group = el.closest('fieldset, [role="radiogroup"], [class*="question"]');
    if (group) {
      const q = group.querySelector('legend, [class*="label"], label:not(:has(input))');
      const qt = q ? clean(q.textContent) : "";
      if (qt && qt !== label) {
        optionLabel = label;
        label = qt;
      }
    }
  }
  const required =
    el.required === true || el.getAttribute("aria-required") === "true" || /[*\\u2731]/.test(label);
  return { label, optionLabel, type, value, checked, required, name: el.getAttribute("name") || "" };
}
"""

_CONTROL_SELECTOR = (
    "input:not([type=hidden]):not([type=submit]):not([type=button])"
    ":not([type=reset]):not([type=image]), select, textarea"
)

Classification = str  # "filled_ok" | "empty_required" | "empty_optional"


@dataclass
class FieldAudit:
    locator: object  # playwright Locator for the concrete element
    label: str
    kind: str  # "text", "email", "tel", "textarea", "select", "checkbox", "radio", "file", ...
    value: str
    required: bool
    name: str = ""
    checked: bool | None = None
    option_label: str = ""  # radios/checkboxes: this option's own text ("Yes")

    @property
    def classification(self) -> Classification:
        if self.kind in ("checkbox", "radio"):
            filled = bool(self.checked)
        else:
            filled = bool(self.value.strip())
        if filled:
            return "filled_ok"
        return "empty_required" if self.required else "empty_optional"


def audit_form_fields(page_or_frame) -> list[FieldAudit]:
    """Describe every visible form control on the page/frame. Controls
    that disappear or error mid-walk are skipped, never fatal."""
    audits: list[FieldAudit] = []
    try:
        controls = page_or_frame.locator(_CONTROL_SELECTOR).all()
    except Exception:
        return audits  # page closed/navigated (e.g. one-click-apply tab) - nothing to audit
    for locator in controls:
        try:
            if not locator.is_visible():
                continue
            info = locator.evaluate(_DESCRIBE_JS)
        except Exception:
            continue
        audits.append(
            FieldAudit(
                locator=locator,
                label=info.get("label") or "",
                kind=info.get("type") or "text",
                value=info.get("value") or "",
                required=bool(info.get("required")),
                name=info.get("name") or "",
                checked=info.get("checked"),
                option_label=info.get("optionLabel") or "",
            )
        )
    return audits


def record_observed_answers(
    audits: list[FieldAudit],
    store: ApplicationDataStore,
    *,
    source: str,
    ats: str | None = None,
    job_id: str | None = None,
) -> int:
    """Store every already-filled text-like field as a learned answer —
    the 'store the data as you fill the applications' behavior. Sensitive
    kinds (password/file) and unlabeled fields are never recorded."""
    recorded = 0
    for audit in audits:
        if audit.kind in ("password", "file", "checkbox", "radio", "hidden"):
            continue
        if not audit.label.strip() or not audit.value.strip():
            continue
        store.record(audit.label, audit.value, source=source, ats=ats, job_id=job_id)
        recorded += 1
    return recorded


def _fill_select(audit: FieldAudit, answer: str) -> bool:
    """Choose the option whose visible text best matches `answer`."""
    try:
        options = audit.locator.evaluate(
            "el => Array.from(el.options).map(o => o.textContent.trim())"
        )
    except Exception:
        return False
    best: tuple[float, str] | None = None
    for option_text in options:
        if not option_text:
            continue
        ratio = SequenceMatcher(None, answer.casefold(), option_text.casefold()).ratio()
        if best is None or ratio > best[0]:
            best = (ratio, option_text)
    if best is None or best[0] < 0.6:
        return False
    try:
        audit.locator.select_option(label=best[1])
        return True
    except Exception:
        return False


def _correct_prefilled_against_human(
    audit: FieldAudit,
    store: ApplicationDataStore,
    report: FillReport,
    *,
    ats: str | None = None,
    job_id: str | None = None,
) -> None:
    """Re-fill an already-filled text/select field when the store holds a
    *human-corrected* answer that contradicts the current value (e.g. the
    extension prefilled Country 'India' but the user fixed it to 'United
    States'). Only human-sourced answers override a prefilled value, so
    normal observed data is never second-guessed."""
    if audit.kind not in ("select", "text", "email", "tel", "textarea"):
        return
    if not audit.label.strip():
        return
    result = store.lookup(audit.label)
    if result is None or result.stored_source != "human":
        return
    if audit.value.strip().casefold() == result.answer.strip().casefold():
        return
    if fill_field(audit, result.answer):
        report.filled.append((audit.label, f"{result.answer} (corrected from {audit.value!r})"))
        store.record(audit.label, result.answer, source="human", ats=ats, job_id=job_id)


_YES_VALUES = {"yes", "true", "checked", "y", "1"}


def _radio_option_matches(option_label: str, answer: str) -> bool:
    option = (option_label or "").strip().casefold()
    wanted = (answer or "").strip().casefold()
    if not option or not wanted:
        return False
    return (
        wanted == option
        or SequenceMatcher(None, wanted, option).ratio() >= 0.8
        or (wanted in _YES_VALUES and option.startswith("yes"))
        or (wanted in ("no", "false", "n", "0") and option.startswith("no"))
    )


def fill_field(audit: FieldAudit, answer: str) -> bool:
    """Fill one control with `answer`. Returns True when the value was
    applied; False (never raises) when this control can't take it.

    Every interaction is preceded by a hover + short pause (and text is
    typed with human cadence, not instant fill) so form-filling produces
    the mousemove/hover/keystroke signals a human would — reducing
    bot-detection risk (spec §6)."""
    try:
        if audit.kind == "select":
            human_hover_locator(audit.locator)
            return _fill_select(audit, answer)
        if audit.kind == "checkbox":
            human_hover_locator(audit.locator)
            should_check = answer.strip().casefold() in _YES_VALUES
            audit.locator.set_checked(should_check)
            return True
        if audit.kind == "radio":
            # check this radio only when the stored answer names THIS
            # option (its own text, e.g. "Yes"), never the group question.
            if _radio_option_matches(audit.option_label or audit.label, answer):
                human_hover_locator(audit.locator)
                audit.locator.set_checked(True)
                return True
            return False
        if audit.kind == "file":
            return False  # resume upload is a separate, deliberate step
        human_fill_locator(audit.locator, answer)
        return True
    except Exception:
        return False


@dataclass
class FillReport:
    filled: list[tuple[str, str]] = field(default_factory=list)  # (label, answer)
    unanswered: list[str] = field(default_factory=list)  # labels with no stored answer
    failed: list[str] = field(default_factory=list)  # had an answer, fill didn't apply

    @property
    def unresolved_required(self) -> list[str]:
        return self.unanswered + self.failed


def fill_missing_fields(
    audits: list[FieldAudit],
    store: ApplicationDataStore,
    *,
    ats: str | None = None,
    job_id: str | None = None,
    include_optional: bool = True,
) -> FillReport:
    """Fill every empty field the store can answer; report the rest.

    Also *verifies* already-answered radio groups against the store: when
    the checked option contradicts the stored answer (e.g. the extension's
    own autofill data answered sponsorship "No" while the user's canonical
    answer is "Yes" - live-observed 2026-07-15), the matching option is
    checked instead and reported as a correction.
    """
    report = FillReport()
    # radio groups: one checked member satisfies the whole group, and an
    # unanswerable group should be reported once, not once per option.
    groups: dict[str, list[FieldAudit]] = {}
    for audit in audits:
        if audit.kind == "radio" and audit.name:
            groups.setdefault(audit.name, []).append(audit)
    satisfied_groups = set()
    for name, members in groups.items():
        checked = next((m for m in members if m.checked), None)
        if checked is None:
            continue
        satisfied_groups.add(name)
        result = store.lookup(checked.label) if checked.label.strip() else None
        if result is None or _radio_option_matches(checked.option_label, result.answer):
            continue
        target = next(
            (m for m in members if _radio_option_matches(m.option_label, result.answer)),
            None,
        )
        if target is not None:
            try:
                target.locator.set_checked(True)
                report.filled.append(
                    (checked.label, f"{result.answer} (corrected from {checked.option_label!r})")
                )
            except Exception:
                report.failed.append(checked.label)
    reported_groups: set[str] = set()
    for audit in audits:
        if audit.classification == "filled_ok":
            _correct_prefilled_against_human(audit, store, report, ats=ats, job_id=job_id)
            continue
        if audit.classification == "empty_optional" and not include_optional:
            continue
        if audit.kind == "radio" and audit.name:
            if audit.name in satisfied_groups:
                continue
            if audit.name in reported_groups:
                continue
        if not audit.label.strip():
            if audit.required:
                report.unanswered.append(f"<unlabeled {audit.kind} field>")
            continue
        result = store.lookup(audit.label)
        if result is None:
            if audit.required:
                # a required referral/reference/emergency-contact name we
                # can't (and must not) answer with the applicant's own name
                # gets "N/A"; an optional one is left blank (user-directed).
                if is_third_party_name_question(audit.label) and fill_field(audit, "N/A"):
                    report.filled.append((audit.label, "N/A"))
                    continue
                report.unanswered.append(audit.label)
                if audit.kind == "radio" and audit.name:
                    reported_groups.add(audit.name)
            continue
        if fill_field(audit, result.answer):
            report.filled.append((audit.label, result.answer))
            store.record(audit.label, result.answer, source="native_fill", ats=ats, job_id=job_id)
            if audit.kind == "radio" and audit.name:
                satisfied_groups.add(audit.name)
        elif audit.kind == "radio" and audit.name:
            # this option didn't match the answer - another radio in the
            # same group may; only report if none ends up matching.
            continue
        elif audit.required:
            report.failed.append(audit.label)
    return report


_ERROR_SELECTORS = (
    "[aria-invalid='true']",
    "[role='alert']",
    ".error-message, .field-error, .invalid-feedback, .artdeco-inline-feedback--error",
)


def find_validation_errors(page_or_frame, *, max_items: int = 20) -> list[str]:
    """Visible inline validation failures on the current step. Returns
    the associated text (or field label for bare aria-invalid controls)."""
    errors: list[str] = []
    for selector in _ERROR_SELECTORS:
        try:
            elements = page_or_frame.locator(selector).all()
        except Exception:
            continue
        for element in elements[:max_items]:
            try:
                if not element.is_visible():
                    continue
                text = (element.text_content() or "").strip()
                if not text:
                    text = element.evaluate(
                        "el => el.getAttribute('aria-label') || el.getAttribute('name') || ''"
                    )
                errors.append(" ".join(text.split())[:200] or selector)
            except Exception:
                continue
        if len(errors) >= max_items:
            break
    return errors[:max_items]


# Advance-button text, in preference order. Final-submit phrasings are
# separate so the loop can gate them behind explicit confirmation.
_NEXT_BUTTON_PHRASES = ("next", "continue", "save and continue", "review", "proceed")
_FINAL_SUBMIT_PHRASES = (
    "submit application",
    "submit your application",
    "send application",
    "submit",
    "apply now",
    "finish",
)


@dataclass
class AdvanceButton:
    locator: object
    text: str
    is_final_submit: bool


def find_advance_button(page_or_frame) -> AdvanceButton | None:
    """The button that moves to the next step (or submits). Non-final
    phrasings win over final ones when both are present."""
    candidates: list[AdvanceButton] = []
    selector = "button, input[type=submit], [role=button]"
    try:
        elements = page_or_frame.locator(selector).all()
    except Exception:
        return None
    for element in elements:
        try:
            if not element.is_visible() or element.is_disabled():
                continue
            text = (element.text_content() or "").strip()
            if not text:
                text = element.evaluate("el => el.value || el.getAttribute('aria-label') || ''")
            text = " ".join(text.split()).casefold()
        except Exception:
            continue
        if not text or len(text) > 60:
            continue
        if any(p in text for p in _NEXT_BUTTON_PHRASES):
            candidates.append(AdvanceButton(element, text, is_final_submit=False))
        elif any(p == text or text.startswith(p) for p in _FINAL_SUBMIT_PHRASES):
            candidates.append(AdvanceButton(element, text, is_final_submit=True))
    for candidate in candidates:
        if not candidate.is_final_submit:
            return candidate
    return candidates[0] if candidates else None


# Resume/CV file-upload support. File inputs are frequently hidden behind
# a styled widget, so match on the input's surrounding context (label,
# ancestors, container text), not just its own label, and upload via
# set_input_files which works on hidden inputs.
_RESUME_FIELD_RE = re.compile(r"resume|curriculum vitae|\bcv\b", re.I)

_FILE_CONTEXT_JS = """
(el) => {
  const clean = (t) => (t || "").replace(/\\s+/g, " ").trim();
  let ctx = clean(el.getAttribute("aria-label") || "") + " " + clean(el.getAttribute("name") || "");
  if (el.id) {
    const l = document.querySelector(`label[for="${CSS.escape(el.id)}"]`);
    if (l) ctx += " " + clean(l.textContent);
  }
  const box = el.closest("div, section, fieldset, li, td");
  if (box) ctx += " " + clean(box.textContent).slice(0, 160);
  return { context: ctx.trim(), files: el.files ? el.files.length : 0 };
}
"""


def _looks_like_resume_field(text: str) -> bool:
    return bool(_RESUME_FIELD_RE.search(text or ""))


def upload_resume(page_or_frame, resume_path) -> list[str]:
    """Attach `resume_path` to every resume/CV file input that has no file
    yet, returning the context labels uploaded to. Skips inputs that
    already hold a file (e.g. the JobRight extension uploaded the resume),
    so this only fills the gap the extension left."""
    uploaded: list[str] = []
    if not resume_path:
        return uploaded
    try:
        inputs = page_or_frame.locator("input[type=file]").all()
    except Exception:
        return uploaded
    for locator in inputs:
        try:
            info = locator.evaluate(_FILE_CONTEXT_JS)
        except Exception:
            continue
        context = info.get("context") or ""
        if not _looks_like_resume_field(context):
            continue
        if info.get("files"):
            continue  # already has a file - don't overwrite the extension's
        try:
            locator.set_input_files(str(resume_path))
            uploaded.append(context[:60] or "resume")
        except Exception:
            continue
    return uploaded


@dataclass
class PageStepResult:
    step: int
    filled: list[tuple[str, str]]
    unresolved_required: list[str]
    validation_errors: list[str]
    advanced: bool
    button_text: str | None = None


@dataclass
class FillLoopResult:
    status: str  # "awaiting_final_submit" | "submitted_click" | "no_advance_button"
    #             | "max_steps_reached" | "blocked_validation_errors"
    steps: list[PageStepResult] = field(default_factory=list)
    final_url: str = ""


def run_fill_loop(
    page,
    store: ApplicationDataStore,
    *,
    job_id: str | None = None,
    ats: str | None = None,
    run_id: str,
    max_steps: int = 10,
    confirm_final_submit=None,  # callable() -> bool; None = never submit
    settle_ms: int = 1500,
    logger=None,
    observed_source: str = "extension_observed",
    resume_path=None,  # local resume/CV file to upload when a field needs one
) -> FillLoopResult:
    """Fill/advance across a multi-step application (spec S6.2.4).

    Per step: audit → record already-filled answers → fill gaps from the
    store → advance → if validation errors appear, re-fill and retry the
    same step once. Stops at the final submit unless
    `confirm_final_submit()` returns True, on an unanswerable required
    field blocking validation, or at `max_steps`.
    """
    result = FillLoopResult(status="max_steps_reached")
    for step in range(1, max_steps + 1):
        audits = audit_form_fields(page)
        record_observed_answers(audits, store, source=observed_source, ats=ats, job_id=job_id)
        report = fill_missing_fields(audits, store, ats=ats, job_id=job_id)
        if resume_path:
            for label in upload_resume(page, resume_path):
                report.filled.append((label, "[resume file uploaded]"))
        store.save()

        button = find_advance_button(page)
        step_result = PageStepResult(
            step=step,
            filled=report.filled,
            unresolved_required=report.unresolved_required,
            validation_errors=[],
            advanced=False,
            button_text=button.text if button else None,
        )
        result.steps.append(step_result)
        if logger is not None:
            log_event(
                logger,
                "fill_loop_step",
                run_id=run_id,
                step=step,
                filled=len(report.filled),
                unresolved_required=len(report.unresolved_required),
                button=step_result.button_text,
            )

        if button is None:
            result.status = "no_advance_button"
            break

        if button.is_final_submit:
            if confirm_final_submit is None or not confirm_final_submit():
                result.status = "awaiting_final_submit"
                break
            human_hover_locator(button.locator)
            button.locator.click()
            page.wait_for_timeout(settle_ms)
            result.status = "submitted_click"
            break

        human_hover_locator(button.locator)
        button.locator.click()
        page.wait_for_timeout(settle_ms)

        errors = find_validation_errors(page)
        if errors:
            step_result.validation_errors = errors
            # fix pass: re-audit (error styling often reveals which
            # fields are actually required) and retry the advance once.
            retry_audits = audit_form_fields(page)
            retry_report = fill_missing_fields(retry_audits, store, ats=ats, job_id=job_id)
            step_result.filled.extend(retry_report.filled)
            store.save()
            retry_button = find_advance_button(page)
            if retry_button is not None and not retry_button.is_final_submit:
                human_hover_locator(retry_button.locator)
                retry_button.locator.click()
                page.wait_for_timeout(settle_ms)
            if find_validation_errors(page):
                result.status = "blocked_validation_errors"
                break
        step_result.advanced = True
    result.final_url = page.url
    return result
