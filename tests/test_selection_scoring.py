"""Tests for src/selection/scoring.py (spec EPIC 4, T4.2 scoring & ranking).

Pure logic - no browser, no tracker. Covers determinism, monotonicity in
each input, and the optional/feature-flagged LLM-rationale hook.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from src.config.settings import Settings
from src.contracts.models import Job
from src.selection.scoring import composite_score, rank_jobs

FIXED_NOW = datetime(2026, 7, 11, tzinfo=UTC)


def _now_fn() -> datetime:
    return FIXED_NOW


def _job(**overrides) -> Job:
    defaults = dict(
        job_id="jr-1",
        title="Backend Engineer",
        company="Acme",
        location="Remote",
        remote_type="remote",
        salary_min=110000,
        salary_max=140000,
        match_score=80,
        posted_at=FIXED_NOW,
        jobright_url="https://jobright.ai/jobs/jr-1",
        raw_description="We use Python and SQL heavily.",
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


def test_composite_score_is_deterministic():
    settings = _settings()
    job = _job()
    score1 = composite_score(job, settings, ["Python", "SQL"], now_fn=_now_fn)
    score2 = composite_score(job, settings, ["Python", "SQL"], now_fn=_now_fn)
    assert score1 == score2


def test_composite_score_monotonic_in_match_score():
    settings = _settings()
    low = composite_score(_job(match_score=40), settings, [], now_fn=_now_fn)
    high = composite_score(_job(match_score=95), settings, [], now_fn=_now_fn)
    assert high > low


def test_composite_score_monotonic_in_keyword_overlap():
    settings = _settings()
    job = _job(raw_description="We use Python, SQL, and AWS heavily.")
    none_matched = composite_score(job, settings, ["Rust", "Go"], now_fn=_now_fn)
    some_matched = composite_score(job, settings, ["Python", "Go"], now_fn=_now_fn)
    all_matched = composite_score(job, settings, ["Python", "SQL"], now_fn=_now_fn)
    assert none_matched < some_matched < all_matched


def test_composite_score_monotonic_in_recency():
    settings = _settings(max_posting_age_days=30)
    recent = composite_score(_job(posted_at=FIXED_NOW), settings, [], now_fn=_now_fn)
    old = composite_score(
        _job(posted_at=FIXED_NOW - timedelta(days=25)), settings, [], now_fn=_now_fn
    )
    assert recent > old


def test_composite_score_monotonic_in_salary_fit():
    settings = _settings(salary_floor=100000)
    low_pay = composite_score(
        _job(salary_min=90000, salary_max=100000), settings, [], now_fn=_now_fn
    )
    high_pay = composite_score(
        _job(salary_min=180000, salary_max=200000), settings, [], now_fn=_now_fn
    )
    assert high_pay > low_pay


def test_unknown_posted_at_scores_neutral_recency():
    settings = _settings()
    unknown = composite_score(_job(posted_at=None), settings, [], now_fn=_now_fn)
    stale_ish = composite_score(
        _job(posted_at=FIXED_NOW - timedelta(days=200)), settings, [], now_fn=_now_fn
    )
    fresh = composite_score(_job(posted_at=FIXED_NOW), settings, [], now_fn=_now_fn)
    assert stale_ish < unknown < fresh


def test_rank_jobs_sorts_by_score_descending():
    settings = _settings()
    jobs = [
        _job(job_id="low", match_score=30),
        _job(job_id="high", match_score=95),
        _job(job_id="mid", match_score=60),
    ]
    ranked = rank_jobs(jobs, settings, [], now_fn=_now_fn)
    assert [sj.job_id for sj in ranked] == ["high", "mid", "low"]


def test_rank_jobs_tie_break_by_job_id_for_reproducibility():
    settings = _settings()
    jobs = [
        _job(job_id="zzz", match_score=80),
        _job(job_id="aaa", match_score=80),
        _job(job_id="mmm", match_score=80),
    ]
    ranked = rank_jobs(jobs, settings, [], now_fn=_now_fn)
    assert [sj.job_id for sj in ranked] == ["aaa", "mmm", "zzz"]


def test_rank_jobs_deterministic_across_calls():
    settings = _settings()
    jobs = [_job(job_id=f"jr-{i}", match_score=50 + i) for i in range(10)]
    ranked1 = [sj.job_id for sj in rank_jobs(jobs, settings, [], now_fn=_now_fn)]
    ranked2 = [sj.job_id for sj in rank_jobs(jobs, settings, [], now_fn=_now_fn)]
    assert ranked1 == ranked2


def test_rank_jobs_without_rationale_fn_leaves_rationale_none():
    settings = _settings(enable_llm_rationale=True, llm_rationale_top_k=2)
    jobs = [_job(job_id="a", match_score=90), _job(job_id="b", match_score=80)]
    ranked = rank_jobs(jobs, settings, [], now_fn=_now_fn, rationale_fn=None)
    assert all(sj.rationale is None for sj in ranked)


def test_rank_jobs_rationale_fn_not_called_when_flag_disabled():
    settings = _settings(enable_llm_rationale=False, llm_rationale_top_k=2)
    jobs = [_job(job_id="a", match_score=90), _job(job_id="b", match_score=80)]
    calls: list[str] = []

    def rationale_fn(job: Job) -> str:
        calls.append(job.job_id)
        return "rationale"

    ranked = rank_jobs(jobs, settings, [], now_fn=_now_fn, rationale_fn=rationale_fn)
    assert calls == []
    assert all(sj.rationale is None for sj in ranked)


def test_rank_jobs_rationale_fn_called_only_for_top_k_when_enabled():
    settings = _settings(enable_llm_rationale=True, llm_rationale_top_k=2)
    jobs = [
        _job(job_id="a", match_score=95),
        _job(job_id="b", match_score=90),
        _job(job_id="c", match_score=40),
    ]
    calls: list[str] = []

    def rationale_fn(job: Job) -> str:
        calls.append(job.job_id)
        return f"rationale-for-{job.job_id}"

    ranked = rank_jobs(jobs, settings, [], now_fn=_now_fn, rationale_fn=rationale_fn)

    assert sorted(calls) == ["a", "b"]  # only the top-2 by score
    by_id = {sj.job_id: sj for sj in ranked}
    assert by_id["a"].rationale == "rationale-for-a"
    assert by_id["b"].rationale == "rationale-for-b"
    assert by_id["c"].rationale is None


def test_rank_jobs_scores_identical_with_or_without_rationale_hook():
    settings = _settings(enable_llm_rationale=True, llm_rationale_top_k=1)
    jobs = [_job(job_id="a", match_score=95), _job(job_id="b", match_score=40)]

    without_hook = rank_jobs(jobs, settings, [], now_fn=_now_fn, rationale_fn=None)
    with_hook = rank_jobs(jobs, settings, [], now_fn=_now_fn, rationale_fn=lambda j: "x")

    assert [sj.score for sj in without_hook] == [sj.score for sj in with_hook]
    assert [sj.job_id for sj in without_hook] == [sj.job_id for sj in with_hook]
