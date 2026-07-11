"""Composite scoring & ranking for the Selection Engine (spec EPIC 4, T4.2).

`composite_score()` computes S4.2.1's weighted composite for a single
survivor job:

    score = w_match      * (match_score / 100)
          + w_keyword    * (skills found in title/description, as a ratio)
          + w_recency    * exp(-age_days / max_posting_age_days)
          + w_salary_fit * (salary headroom above salary_floor, capped at 1.0)

Weights come from `settings.score_weight_*` (see `src/config/settings.py`;
expected, and validated there, to sum to 1.0).

`rank_jobs()` scores a whole list of survivors, sorts them deterministically
(score descending, `job_id` ascending as a tie-break for reproducibility),
and optionally drives S4.2.2's LLM-rationale pass: a `rationale_fn(job) ->
str` hook, invoked only for the top `settings.llm_rationale_top_k` ranked
jobs, and ONLY when both a `rationale_fn` is supplied by the caller AND
`settings.enable_llm_rationale` is True. Omitting `rationale_fn` (or leaving
the flag off) must - and does - leave every score/rank unchanged; it is a
purely additive annotation pass, never a scoring input.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from src.config.settings import Settings
from src.contracts.models import Job

_NEUTRAL_RECENCY_SCORE = 0.5
_NEUTRAL_SALARY_SCORE = 0.5
# How many multiples of salary_floor count as "fully saturated" (1.0)
# salary fit - a job paying >= 1.5x the floor scores the max, avoiding an
# unbounded score for very high salaries while still rewarding pay above
# the floor.
_SALARY_FIT_SATURATION_RATIO = 1.5


@dataclass(frozen=True)
class ScoredJob:
    """One job's ranking result: its composite score and, if the optional
    LLM-rationale pass ran for it, a short rationale string."""

    job_id: str
    score: float
    rationale: str | None = None


def _match_score_component(job: Job) -> float:
    if job.match_score is None:
        return 0.0
    return max(0.0, min(job.match_score, 100.0)) / 100.0


def _keyword_overlap_component(job: Job, skills: list[str]) -> float:
    """Ratio of `skills` found (case-insensitively, substring match)
    somewhere in the job's title or raw description. Empty `skills` ->
    0.0 (no signal to score, not a free pass)."""
    if not skills:
        return 0.0
    haystack = f"{job.title} {job.raw_description}".casefold()
    hits = sum(1 for skill in skills if skill and skill.casefold() in haystack)
    return hits / len(skills)


def _recency_component(
    job: Job,
    max_posting_age_days: int,
    *,
    now_fn: Callable[[], datetime],
) -> float:
    """Exponential decay by posting age: 1.0 at age 0, decaying toward 0
    as age grows, scaled by `max_posting_age_days` (a job exactly that old
    scores ~0.37). An unknown `posted_at` scores a neutral 0.5 - neither
    rewarded nor penalized, matching dedupe.is_stale()'s own "unknown is
    not positively known to be stale" stance."""
    if job.posted_at is None:
        return _NEUTRAL_RECENCY_SCORE
    posted_at = job.posted_at
    if posted_at.tzinfo is None:
        posted_at = posted_at.replace(tzinfo=UTC)
    age_days = max((now_fn() - posted_at).total_seconds() / 86400.0, 0.0)
    if max_posting_age_days <= 0:
        return 0.0
    return math.exp(-age_days / max_posting_age_days)


def _salary_fit_component(job: Job, salary_floor: int | None) -> float:
    """How comfortably the job's compensation clears `salary_floor`,
    saturating at 1.0 once pay reaches `_SALARY_FIT_SATURATION_RATIO`x the
    floor. No floor configured -> full credit (nothing to fit against);
    unknown salary -> neutral 0.5, mirroring the hard filter's
    unknown-salary handling philosophy (a configurable policy decides
    whether to reject it outright; scoring itself stays neutral)."""
    if salary_floor is None or salary_floor <= 0:
        return 1.0
    effective = job.salary_max if job.salary_max is not None else job.salary_min
    if effective is None:
        return _NEUTRAL_SALARY_SCORE
    ratio = effective / salary_floor
    return max(0.0, min(ratio / _SALARY_FIT_SATURATION_RATIO, 1.0))


def composite_score(
    job: Job,
    settings: Settings,
    skills: list[str],
    *,
    now_fn: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> float:
    """S4.2.1: the weighted composite score for one job. Pure function of
    its inputs - deterministic, and monotonic in each individual
    sub-score's own direction (higher match_score, more skill overlap,
    more recent posting, or better salary fit each only ever raise the
    total, never lower it, since every weight is non-negative)."""
    match = _match_score_component(job)
    keyword_overlap = _keyword_overlap_component(job, skills)
    recency = _recency_component(job, settings.max_posting_age_days, now_fn=now_fn)
    salary_fit = _salary_fit_component(job, settings.salary_floor)

    return (
        settings.score_weight_match * match
        + settings.score_weight_keyword_overlap * keyword_overlap
        + settings.score_weight_recency * recency
        + settings.score_weight_salary_fit * salary_fit
    )


def rank_jobs(
    jobs: list[Job],
    settings: Settings,
    skills: list[str],
    *,
    now_fn: Callable[[], datetime] = lambda: datetime.now(UTC),
    rationale_fn: Callable[[Job], str] | None = None,
) -> list[ScoredJob]:
    """Score every job in `jobs`, then sort deterministically (score
    descending, `job_id` ascending tie-break).

    S4.2.2's optional LLM-rationale pass: if `rationale_fn` is provided
    AND `settings.enable_llm_rationale` is True, `rationale_fn(job)` is
    called for exactly the top `settings.llm_rationale_top_k` ranked jobs
    (by the already-computed, rationale-independent ranking) and the
    result is attached to each `ScoredJob.rationale`. Never called
    otherwise - the pipeline produces identical scores and ordering with
    or without this hook (only `.rationale` differs, which is never used
    as a ranking input).

    `now_fn` is called exactly ONCE here, up front, and that single frozen
    instant is reused for every job's recency component - not re-called
    per job. Calling a real wall-clock `now_fn` fresh per job would let
    recency scores drift by however long the scoring loop takes to run
    (irrelevant for a handful of jobs, but measurable across a few hundred
    - and, with match/keyword/salary components tied, recency becomes the
    sole tie-breaker before job_id, so that drift could otherwise flip the
    ranking of near-identical jobs between two calls of this function).
    Freezing "now" once keeps ranking a pure function of (jobs, settings,
    skills, one instant) - the actual determinism the spec's DoD needs.
    """
    frozen_now = now_fn()

    def _frozen_now_fn() -> datetime:
        return frozen_now

    by_id = {job.job_id: job for job in jobs}
    scored = [
        ScoredJob(
            job_id=job.job_id,
            score=composite_score(job, settings, skills, now_fn=_frozen_now_fn),
        )
        for job in jobs
    ]
    scored.sort(key=lambda sj: (-sj.score, sj.job_id))

    if rationale_fn is not None and settings.enable_llm_rationale:
        top_k = scored[: settings.llm_rationale_top_k]
        for i, scored_job in enumerate(top_k):
            rationale = rationale_fn(by_id[scored_job.job_id])
            scored[i] = ScoredJob(
                job_id=scored_job.job_id, score=scored_job.score, rationale=rationale
            )

    return scored
