"""Unit tests for src/executor/application_data.py (no browser)."""

from __future__ import annotations

from pathlib import Path

import pytest
from src.config.profile import Profile
from src.executor.application_data import (
    ApplicationDataStore,
    RecordedAnswer,
    normalize_question,
)


def make_profile(**overrides) -> Profile:
    base = dict(
        full_name="Alex Kim",
        email="alex.kim@example.com",
        phone="+1-555-0100",
        linkedin_url="https://www.linkedin.com/in/alexkim",
        work_authorized="yes",
        requires_sponsorship="no",
        notice_period_days=14,
        salary_expectation={"minimum": 120000, "maximum": 150000},
        address={"city": "San Jose", "state": "CA", "postal_code": "95112", "country": "USA"},
    )
    base.update(overrides)
    return Profile(**base)


def make_store(tmp_path: Path, **profile_overrides) -> ApplicationDataStore:
    return ApplicationDataStore(
        make_profile(**profile_overrides), store_path=tmp_path / "application_data.yaml"
    )


class TestNormalizeQuestion:
    def test_strips_required_markers_punctuation_and_case(self):
        assert normalize_question("First Name *") == "first name"
        assert normalize_question("  Email:  (required) ") == "email"

    def test_collapses_whitespace(self):
        assert normalize_question("last\n  name") == "last name"


class TestProfileLookup:
    def test_name_parts(self, tmp_path):
        store = make_store(tmp_path)
        assert store.lookup("First Name *").answer == "Alex"
        assert store.lookup("Last name").answer == "Kim"
        assert store.lookup("Full legal name").answer == "Alex Kim"

    def test_contact_fields(self, tmp_path):
        store = make_store(tmp_path)
        assert store.lookup("Email address").answer == "alex.kim@example.com"
        assert store.lookup("Phone number").answer == "+1-555-0100"
        assert store.lookup("City").answer == "San Jose"

    def test_authorization_answers_render_as_yes_no(self, tmp_path):
        store = make_store(tmp_path)
        assert store.lookup("Are you legally authorized to work in the US?").answer == "Yes"
        assert store.lookup("Will you require visa sponsorship?").answer == "No"

    def test_declined_fields_are_never_auto_answered(self, tmp_path):
        store = make_store(tmp_path, work_authorized="decline_to_answer")
        assert store.lookup("Are you authorized to work in the US?") is None
        assert store.lookup("What is your gender?") is None  # EEO defaults to decline

    def test_unknown_question_returns_none(self, tmp_path):
        store = make_store(tmp_path)
        assert store.lookup("Describe your favorite sorting algorithm") is None
        assert store.lookup("") is None


class TestRecordAndLookup:
    def test_recorded_answer_found_exactly_despite_formatting(self, tmp_path):
        store = make_store(tmp_path)
        store.record(
            "How many years of experience with Python do you have?",
            "4",
            source="human",
        )
        result = store.lookup("how many years of experience with python do you have")
        assert result is not None
        assert result.answer == "4"
        assert result.source == "stored_exact"

    def test_fuzzy_match_above_threshold(self, tmp_path):
        store = make_store(tmp_path)
        store.record(
            "How many years of experience with Python do you have?",
            "4",
            source="human",
        )
        result = store.lookup("How many years experience with Python do you have?")
        assert result is not None
        assert result.source == "stored_fuzzy"
        assert result.answer == "4"

    def test_dissimilar_question_does_not_fuzzy_match(self, tmp_path):
        store = make_store(tmp_path)
        store.record("Years of experience with Python?", "4", source="human")
        assert store.lookup("Do you have felony convictions?") is None

    def test_stored_answer_wins_over_profile_heuristic(self, tmp_path):
        store = make_store(tmp_path)
        store.record("Email", "work.alias@example.com", source="human")
        assert store.lookup("Email").answer == "work.alias@example.com"

    def test_same_normalized_question_updates_instead_of_duplicating(self, tmp_path):
        store = make_store(tmp_path)
        store.record("Notice period?", "2 weeks", source="extension_observed")
        store.record("notice period", "30 days", source="native_fill")
        assert len(store.answers) == 1
        assert store.lookup("Notice Period *").answer == "30 days"

    def test_human_answer_not_downgraded_by_observed_source(self, tmp_path):
        store = make_store(tmp_path)
        store.record("Country", "United States", source="human")
        # a later extension/native observation of the same field must NOT
        # overwrite the human correction
        store.record("country", "India", source="extension_observed")
        result = store.lookup("* Country")
        assert result.answer == "United States"
        assert result.stored_source == "human"

    def test_human_answer_can_be_re_corrected_by_human(self, tmp_path):
        store = make_store(tmp_path)
        store.record("Country", "United States", source="human")
        store.record("country", "Canada", source="human")
        assert store.lookup("Country").answer == "Canada"

    def test_third_party_name_fields_not_answered_with_own_name(self, tmp_path):
        store = make_store(tmp_path, full_name="Alex Kim")
        assert store.lookup("First Name").answer == "Alex"  # own name still works
        for q in (
            "If you were referred by an employee please enter their name:",
            "Reference name",
            "Emergency contact name",
            "Supervisor name",
        ):
            assert store.lookup(q) is None, q

    def test_blank_question_or_answer_rejected(self, tmp_path):
        store = make_store(tmp_path)
        with pytest.raises(ValueError):
            store.record("", "something", source="human")
        with pytest.raises(ValueError):
            store.record("A question", "   ", source="human")


class TestPersistence:
    def test_save_then_load_roundtrip(self, tmp_path):
        profile_path = tmp_path / "profile.yaml"
        profile_path.write_text("full_name: Alex Kim\nemail: alex.kim@example.com\n")
        store_path = tmp_path / "application_data.yaml"

        store = ApplicationDataStore(make_profile(), store_path=store_path)
        store.record("Preferred pronouns", "they/them", source="human", ats="greenhouse")
        saved = store.save()
        assert saved == store_path

        reloaded = ApplicationDataStore.load(profile_path=profile_path, store_path=store_path)
        result = reloaded.lookup("Preferred pronouns")
        assert result is not None
        assert result.answer == "they/them"
        assert reloaded.answers[0].ats == "greenhouse"

    def test_load_without_store_file_starts_empty(self, tmp_path):
        profile_path = tmp_path / "profile.yaml"
        profile_path.write_text("full_name: Alex Kim\n")
        store = ApplicationDataStore.load(
            profile_path=profile_path, store_path=tmp_path / "missing.yaml"
        )
        assert store.answers == []

    def test_constructor_accepts_preloaded_answers(self, tmp_path):
        answers = [RecordedAnswer(question="Q", answer="A", source="human")]
        store = ApplicationDataStore(make_profile(), answers, store_path=tmp_path / "s.yaml")
        assert store.lookup("Q").answer == "A"
