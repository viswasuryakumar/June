"""Tests for src/selection/filters.py (spec EPIC 4, T4.1 hard filters).

Pure logic - no browser, no tracker. Each hard filter is tested in
isolation to confirm it returns its own specific, stable reason code.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from src.config.settings import LocationRules, Settings
from src.contracts.models import Job
from src.selection.filters import (
    REASON_BELOW_MIN_MATCH_SCORE,
    REASON_BELOW_SALARY_FLOOR,
    REASON_BLOCKLISTED_COMPANY,
    REASON_LOCATION_POLICY_VIOLATION,
    REASON_POSTING_TOO_OLD,
    REASON_TITLE_EXCLUDED,
    REASON_TITLE_NOT_INCLUDED,
    passes_hard_filters,
)

FIXED_NOW = datetime(2026, 7, 11, tzinfo=UTC)


def _now_fn() -> datetime:
    return FIXED_NOW


def _job(**overrides) -> Job:
    defaults = dict(
        job_id="jr-1",
        title="Senior Backend Engineer",
        company="Acme",
        location="Remote",
        remote_type="remote",
        salary_min=110000,
        salary_max=140000,
        match_score=85,
        posted_at=FIXED_NOW,
        jobright_url="https://jobright.ai/jobs/jr-1",
    )
    defaults.update(overrides)
    return Job(**defaults)


def _settings(**overrides) -> Settings:
    defaults = dict(
        max_applications_per_day=5,
        min_match_score=70,
        salary_floor=100000,
        max_posting_age_days=30,
    )
    defaults.update(overrides)
    return Settings(**defaults)


def test_job_that_passes_every_filter_is_accepted():
    ok, reason = passes_hard_filters(_job(), _settings(), now_fn=_now_fn)
    assert ok is True
    assert reason is None


def test_below_min_match_score_rejected():
    ok, reason = passes_hard_filters(_job(match_score=50), _settings(), now_fn=_now_fn)
    assert ok is False
    assert reason == REASON_BELOW_MIN_MATCH_SCORE


def test_missing_match_score_treated_as_below_threshold():
    ok, reason = passes_hard_filters(_job(match_score=None), _settings(), now_fn=_now_fn)
    assert ok is False
    assert reason == REASON_BELOW_MIN_MATCH_SCORE


def test_title_excluded_by_regex():
    settings = _settings(title_exclude_regexes=[r"(?i)senior"])
    ok, reason = passes_hard_filters(_job(), settings, now_fn=_now_fn)
    assert ok is False
    assert reason == REASON_TITLE_EXCLUDED


def test_exclude_wins_over_include_when_both_match():
    settings = _settings(
        title_include_regexes=[r"(?i)engineer"],
        title_exclude_regexes=[r"(?i)senior"],
    )
    ok, reason = passes_hard_filters(_job(), settings, now_fn=_now_fn)
    assert ok is False
    assert reason == REASON_TITLE_EXCLUDED


def test_title_not_included_when_include_list_has_no_match():
    settings = _settings(title_include_regexes=[r"(?i)frontend"])
    ok, reason = passes_hard_filters(_job(), settings, now_fn=_now_fn)
    assert ok is False
    assert reason == REASON_TITLE_NOT_INCLUDED


def test_empty_include_list_does_not_reject():
    settings = _settings(title_include_regexes=[])
    ok, _ = passes_hard_filters(_job(), settings, now_fn=_now_fn)
    assert ok is True


@pytest.mark.parametrize(
    "remote_policy,remote_type,expected_ok",
    [
        ("remote_only", "remote", True),
        ("remote_only", "hybrid", False),
        ("remote_only", "onsite", False),
        ("hybrid_ok", "hybrid", True),
        ("hybrid_ok", "onsite", False),
        ("onsite_ok", "onsite", True),
        ("onsite_ok", "unknown", False),
        ("any", "unknown", True),
    ],
)
def test_remote_policy_matrix(remote_policy, remote_type, expected_ok):
    settings = _settings(location_rules=LocationRules(remote_policy=remote_policy))
    ok, reason = passes_hard_filters(_job(remote_type=remote_type), settings, now_fn=_now_fn)
    assert ok is expected_ok
    if not expected_ok:
        assert reason == REASON_LOCATION_POLICY_VIOLATION


def test_disallowed_location_rejected():
    settings = _settings(
        location_rules=LocationRules(remote_policy="any", disallowed_locations=["Austin"])
    )
    ok, reason = passes_hard_filters(
        _job(remote_type="onsite", location="Austin, TX"), settings, now_fn=_now_fn
    )
    assert ok is False
    assert reason == REASON_LOCATION_POLICY_VIOLATION


def test_allowed_locations_restricts_to_list():
    settings = _settings(
        location_rules=LocationRules(remote_policy="any", allowed_locations=["New York"])
    )
    ok, reason = passes_hard_filters(
        _job(remote_type="onsite", location="Austin, TX"), settings, now_fn=_now_fn
    )
    assert ok is False
    assert reason == REASON_LOCATION_POLICY_VIOLATION

    ok2, _ = passes_hard_filters(
        _job(remote_type="onsite", location="New York, NY"), settings, now_fn=_now_fn
    )
    assert ok2 is True


def test_below_salary_floor_rejected():
    settings = _settings(salary_floor=150000)
    ok, reason = passes_hard_filters(
        _job(salary_min=100000, salary_max=120000), settings, now_fn=_now_fn
    )
    assert ok is False
    assert reason == REASON_BELOW_SALARY_FLOOR


def test_unknown_salary_accepted_by_default_policy():
    settings = _settings(salary_floor=150000, unknown_salary_policy="accept")
    ok, _ = passes_hard_filters(_job(salary_min=None, salary_max=None), settings, now_fn=_now_fn)
    assert ok is True


def test_unknown_salary_rejected_when_policy_is_reject():
    settings = _settings(salary_floor=150000, unknown_salary_policy="reject")
    ok, reason = passes_hard_filters(
        _job(salary_min=None, salary_max=None), settings, now_fn=_now_fn
    )
    assert ok is False
    assert reason == REASON_BELOW_SALARY_FLOOR


def test_no_salary_floor_configured_never_rejects_on_salary():
    settings = _settings(salary_floor=None, unknown_salary_policy="reject")
    ok, _ = passes_hard_filters(_job(salary_min=None, salary_max=None), settings, now_fn=_now_fn)
    assert ok is True


def test_blocklisted_company_rejected_case_insensitively():
    settings = _settings(blocklisted_companies=["ACME"])
    ok, reason = passes_hard_filters(_job(company="acme"), settings, now_fn=_now_fn)
    assert ok is False
    assert reason == REASON_BLOCKLISTED_COMPANY


def test_posting_too_old_rejected():
    settings = _settings(max_posting_age_days=10)
    old_post = FIXED_NOW.replace(year=2026, month=6, day=1)
    ok, reason = passes_hard_filters(_job(posted_at=old_post), settings, now_fn=_now_fn)
    assert ok is False
    assert reason == REASON_POSTING_TOO_OLD


def test_unknown_posted_at_never_rejected_for_age():
    settings = _settings(max_posting_age_days=10)
    ok, _ = passes_hard_filters(_job(posted_at=None), settings, now_fn=_now_fn)
    assert ok is True


def test_filter_order_is_deterministic_first_failure_wins():
    # A job that fails BOTH min-match-score and the (looser) title check
    # should always report the earlier check (min match score) - proving
    # a fixed, deterministic filter order rather than dict-iteration luck.
    settings = _settings(
        min_match_score=90,
        title_include_regexes=[r"(?i)frontend"],
    )
    ok, reason = passes_hard_filters(_job(match_score=10), settings, now_fn=_now_fn)
    assert ok is False
    assert reason == REASON_BELOW_MIN_MATCH_SCORE
