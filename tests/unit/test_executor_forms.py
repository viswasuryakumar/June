"""Component tests for src/executor/forms.py against local data: URL
fixture forms (same pattern as tests/test_selector_broken.py)."""

from __future__ import annotations

import pytest
from playwright.sync_api import sync_playwright
from src.config.profile import Profile
from src.executor.application_data import ApplicationDataStore
from src.executor.forms import (
    audit_form_fields,
    fill_missing_fields,
    find_advance_button,
    find_validation_errors,
    record_observed_answers,
    run_fill_loop,
    upload_resume,
)

pytestmark = pytest.mark.playwright


@pytest.fixture(scope="module")
def page():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, channel="chromium")
        page = browser.new_page()
        yield page
        browser.close()


def make_store(tmp_path, **profile_overrides):
    base = dict(
        full_name="Alex Kim",
        email="alex.kim@example.com",
        phone="+1-555-0100",
        work_authorized="yes",
        address={"city": "San Jose"},
    )
    base.update(profile_overrides)
    return ApplicationDataStore(Profile(**base), store_path=tmp_path / "store.yaml")


FORM_HTML = """data:text/html,
<form>
  <label for="fn">First Name *</label><input id="fn" required>
  <label>Last Name <input name="last_name"></label>
  <input aria-label="Email" type="email" value="prefilled@example.com">
  <input placeholder="Phone number" type="tel">
  <label for="auth">Are you authorized to work in the US?</label>
  <select id="auth"><option value="">Select...</option><option>Yes</option><option>No</option></select>
  <label><input type="checkbox" name="agree" required> I agree to the terms</label>
  <input type="hidden" name="csrf" value="x">
  <input type="password" aria-label="Account password" value="hunter2">
  <button type="button">Next</button>
</form>
"""


class TestAuditFormFields:
    def test_labels_types_values_and_required(self, page):
        page.goto(FORM_HTML)
        audits = {a.label: a for a in audit_form_fields(page)}

        assert "csrf" not in [a.name for a in audits.values()]  # hidden skipped
        first = audits["First Name *"]
        assert first.kind == "text" and first.required
        assert first.classification == "empty_required"

        assert audits["Last Name"].classification == "empty_optional"
        email = audits["Email"]
        assert email.value == "prefilled@example.com"
        assert email.classification == "filled_ok"
        assert audits["Are you authorized to work in the US?"].kind == "select"
        agree = audits["I agree to the terms"]
        assert agree.kind == "checkbox" and agree.classification == "empty_required"


class TestCorrectPrefilledAgainstHuman:
    def test_human_answer_corrects_wrong_prefilled_select(self, page, tmp_path):
        page.goto(
            "data:text/html,<form>"
            "<label for='c'>Country</label>"
            "<select id='c'><option>India</option><option>United States</option></select>"
            "</form>"
        )
        # a prefilled (wrong) value on the control
        page.select_option("#c", label="India")
        store = make_store(tmp_path)
        store.record("Country", "United States", source="human")
        report = fill_missing_fields(audit_form_fields(page), store)
        assert page.locator("#c").input_value() == "United States"
        assert any("corrected from" in a for _, a in report.filled)

    def test_non_human_answer_does_not_override_prefilled(self, page, tmp_path):
        page.goto(
            "data:text/html,<form>"
            "<label for='c'>Country</label>"
            "<select id='c'><option>India</option><option>United States</option></select>"
            "</form>"
        )
        page.select_option("#c", label="India")
        store = make_store(tmp_path)
        store.record("Country", "United States", source="extension_observed")
        fill_missing_fields(audit_form_fields(page), store)
        assert page.locator("#c").input_value() == "India"  # left as-is


class TestReferralNameField:
    def test_required_referral_name_filled_na(self, page, tmp_path):
        page.goto(
            "data:text/html,<form>"
            "<label for='r'>If you were referred by an employee please enter their name *</label>"
            "<input id='r' required>"
            "</form>"
        )
        store = make_store(tmp_path, full_name="Alex Kim")
        report = fill_missing_fields(audit_form_fields(page), store)
        assert page.locator("#r").input_value() == "N/A"
        assert any(ans == "N/A" for _, ans in report.filled)

    def test_optional_referral_name_left_blank(self, page, tmp_path):
        page.goto(
            "data:text/html,<form>"
            "<label for='r'>Referral: enter their name</label><input id='r'>"
            "</form>"
        )
        store = make_store(tmp_path, full_name="Alex Kim")
        fill_missing_fields(audit_form_fields(page), store)
        assert page.locator("#r").input_value() == ""


class TestUploadResume:
    def test_uploads_to_empty_cv_field(self, page, tmp_path):
        resume = tmp_path / "resume.pdf"
        resume.write_bytes(b"%PDF-1.4 fake")
        page.goto(
            "data:text/html,<form>"
            "<label for='cv'>Upload a CV</label><input id='cv' type='file'>"
            "<label for='other'>Photo</label><input id='other' type='file'>"
            "</form>"
        )
        uploaded = upload_resume(page, resume)
        assert len(uploaded) == 1
        assert page.locator("#cv").evaluate("el => el.files.length") == 1
        assert page.locator("#other").evaluate("el => el.files.length") == 0

    def test_skips_field_that_already_has_a_file(self, page, tmp_path):
        resume = tmp_path / "resume.pdf"
        resume.write_bytes(b"%PDF-1.4 fake")
        page.goto(
            "data:text/html,<form>"
            "<label for='cv'>Resume</label><input id='cv' type='file'>"
            "</form>"
        )
        page.set_input_files("#cv", str(resume))
        assert upload_resume(page, resume) == []

    def test_none_path_is_a_noop(self, page):
        page.goto("data:text/html,<form><input type='file' aria-label='Resume'></form>")
        assert upload_resume(page, None) == []


class TestRecordObservedAnswers:
    def test_prefilled_values_are_learned_but_passwords_never(self, page, tmp_path):
        page.goto(FORM_HTML)
        store = make_store(tmp_path)
        recorded = record_observed_answers(
            audit_form_fields(page), store, source="extension_observed", job_id="j1"
        )
        assert recorded == 1  # only the prefilled email
        assert store.lookup("Email").answer == "prefilled@example.com"
        assert store.lookup("Account password") is None


class TestFillMissingFields:
    def test_fills_text_select_and_checkbox_and_reports_gaps(self, page, tmp_path):
        page.goto(FORM_HTML)
        store = make_store(tmp_path)
        store.record("I agree to the terms", "yes", source="human")

        report = fill_missing_fields(audit_form_fields(page), store, job_id="j1")

        assert page.locator("#fn").input_value() == "Alex"
        assert page.locator("input[type=tel]").input_value() == "+1-555-0100"
        assert page.locator("#auth").evaluate("el => el.selectedOptions[0].textContent") == "Yes"
        assert page.locator("input[name=agree]").is_checked()
        filled_labels = [label for label, _ in report.filled]
        assert "First Name *" in filled_labels
        assert report.unresolved_required == []

    def test_unanswerable_required_field_is_reported_not_guessed(self, page, tmp_path):
        page.goto(
            "data:text/html,<form>"
            "<label for='q'>Explain a time you failed *</label>"
            "<input id='q' required><button>Next</button></form>"
        )
        store = make_store(tmp_path)
        report = fill_missing_fields(audit_form_fields(page), store)
        assert report.unanswered == ["Explain a time you failed *"]
        assert page.locator("#q").input_value() == ""


class TestValidationErrorsAndAdvanceButton:
    def test_visible_alerts_and_aria_invalid_are_reported(self, page):
        page.goto(
            "data:text/html,"
            "<div role='alert'>Email is required</div>"
            "<input aria-invalid='true' aria-label='Email'>"
            "<div role='alert' style='display:none'>hidden error</div>"
        )
        errors = find_validation_errors(page)
        assert "Email is required" in errors
        assert not any("hidden error" in e for e in errors)

    def test_next_button_preferred_over_final_submit(self, page):
        page.goto("data:text/html,<button>Submit application</button><button>Next</button>")
        button = find_advance_button(page)
        assert button is not None
        assert button.text == "next"
        assert not button.is_final_submit

    def test_lone_submit_is_final(self, page):
        page.goto("data:text/html,<button>Submit application</button>")
        button = find_advance_button(page)
        assert button.is_final_submit

    def test_no_button_returns_none(self, page):
        page.goto("data:text/html,<p>Thanks!</p>")
        assert find_advance_button(page) is None


# Two-step form: step 1 requires an email; clicking Next shows an inline
# error while it is empty, and swaps to step 2 (final submit) once filled.
MULTISTEP_HTML = """data:text/html,
<div id="step1">
  <label for="em">Email *</label><input id="em" required>
  <div id="err" role="alert" style="display:none">Email is required</div>
  <button type="button" onclick="
    if (!document.getElementById('em').value) {
      document.getElementById('err').style.display = 'block';
    } else {
      document.getElementById('step1').remove();
      document.getElementById('step2').style.display = 'block';
    }">Next</button>
</div>
<div id="step2" style="display:none">
  <p>Review your application</p>
  <button type="button">Submit application</button>
</div>
"""


class TestRunFillLoop:
    def test_fills_advances_and_stops_at_final_submit(self, page, tmp_path):
        page.goto(MULTISTEP_HTML)
        store = make_store(tmp_path)
        result = run_fill_loop(
            page, store, job_id="j1", run_id="test-run", max_steps=5, settle_ms=50
        )
        assert result.status == "awaiting_final_submit"
        assert page.locator("#step2").is_visible()  # really advanced
        assert result.steps[0].advanced
        assert ("Email *", "alex.kim@example.com") in result.steps[0].filled

    def test_confirmed_final_submit_is_clicked(self, page, tmp_path):
        page.goto(
            "data:text/html,<button type='button' "
            "onclick=\"this.textContent='Done!'\">Submit application</button>"
        )
        store = make_store(tmp_path)
        result = run_fill_loop(
            page,
            store,
            run_id="test-run",
            settle_ms=50,
            confirm_final_submit=lambda: True,
        )
        assert result.status == "submitted_click"
        assert page.locator("button").text_content() == "Done!"

    def test_unfixable_validation_error_blocks_the_loop(self, page, tmp_path):
        # required field the store cannot answer -> error appears, the fix
        # pass cannot fill it either -> loop reports blocked, not success.
        page.goto(
            MULTISTEP_HTML.replace("Email *", "Spirit animal *").replace(
                "Email is required", "Spirit animal is required"
            )
        )
        store = make_store(tmp_path)
        result = run_fill_loop(page, store, run_id="test-run", max_steps=5, settle_ms=50)
        assert result.status == "blocked_validation_errors"
        assert "Spirit animal *" in result.steps[0].unresolved_required

    def test_page_without_form_or_buttons_ends_cleanly(self, page, tmp_path):
        page.goto("data:text/html,<p>Application received!</p>")
        store = make_store(tmp_path)
        result = run_fill_loop(page, store, run_id="test-run", settle_ms=50)
        assert result.status == "no_advance_button"


# Lever-style radio group: the question lives on the container's label
# div, each radio's own label is just "Yes"/"No" (live-observed 2026-07-15).
RADIO_GROUP_HTML = """data:text/html,
<ul><li class="application-question">
  <div class="application-label">Will you now or in the future require sponsorship
    for employment visa status (e.g. H-1B status)?%E2%9C%B1</div>
  <div class="application-field">
    <label><input type="radio" name="sponsorship" value="yes" required> Yes</label>
    <label><input type="radio" name="sponsorship" value="no" required> No</label>
  </div>
</li></ul>
"""


class TestRadioGroups:
    def test_group_question_and_option_labels_extracted(self, page):
        page.goto(RADIO_GROUP_HTML)
        radios = [a for a in audit_form_fields(page) if a.kind == "radio"]
        assert len(radios) == 2
        assert all("require sponsorship" in a.label for a in radios)
        assert [a.option_label for a in radios] == ["Yes", "No"]
        assert all(a.required for a in radios)

    def test_sponsorship_answer_checks_the_matching_option(self, page, tmp_path):
        page.goto(RADIO_GROUP_HTML)
        store = make_store(tmp_path, requires_sponsorship="yes")
        report = fill_missing_fields(audit_form_fields(page), store)
        assert page.locator("input[value=yes]").is_checked()
        assert not page.locator("input[value=no]").is_checked()
        assert report.unresolved_required == []

    def test_unanswerable_group_reported_once_not_per_option(self, page, tmp_path):
        page.goto(
            RADIO_GROUP_HTML.replace("require sponsorship", "enjoy skydiving").replace(
                "sponsorship\n", "skydiving\n"
            )
        )
        store = make_store(tmp_path, requires_sponsorship="decline_to_answer")
        report = fill_missing_fields(audit_form_fields(page), store)
        assert len(report.unresolved_required) == 1

    def test_group_with_checked_member_is_not_reported(self, page, tmp_path):
        page.goto(RADIO_GROUP_HTML)
        page.locator("input[value=no]").check()
        store = make_store(tmp_path, requires_sponsorship="decline_to_answer")
        report = fill_missing_fields(audit_form_fields(page), store)
        assert report.unresolved_required == []

    def test_wrong_extension_answer_is_corrected_to_stored_answer(self, page, tmp_path):
        # the extension checked "No" for sponsorship; the user's canonical
        # answer is yes -> the verify pass must flip it to "Yes".
        page.goto(RADIO_GROUP_HTML)
        page.locator("input[value=no]").check()
        store = make_store(tmp_path, requires_sponsorship="yes")
        report = fill_missing_fields(audit_form_fields(page), store)
        assert page.locator("input[value=yes]").is_checked()
        assert not page.locator("input[value=no]").is_checked()
        assert any("corrected" in answer for _, answer in report.filled)
        assert report.unresolved_required == []
