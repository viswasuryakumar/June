"""Selection Engine top-level orchestration (spec EPIC 4).

`select_and_queue()` ties T4.1 (hard filters), T4.2 (scoring/ranking), and
T4.3 (daily quota + queueing) together into the single entrypoint other
epics/the orchestrator should call, mirroring
`src/discovery/sync.py:sync_jobs()`'s shape: one `*RunResult` dataclass +
one orchestration function, fully dependency-injectable (tracker, settings,
skills, run_id, logger, now_fn, rationale_fn) - no globals.

Pure logic: no Playwright page is involved anywhere in this module. It only
talks to the tracker through `TrackerRepository`'s existing interface.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

from src.config.settings import Settings
from src.contracts.models import HITLTicket, Job
from src.observability.logging import log_event
from src.selection.filters import passes_hard_filters
from src.selection.scoring import rank_jobs

# HITL tickets for a batch selection approval (S4.3.3) aren't tied to any
# single job - mirrors src/auth/login.py's SESSION_JOB_ID sentinel-job_id
# pattern for tickets that gate a whole run rather than one specific job.
BATCH_SELECTION_JOB_ID = "batch-selection"

# Reason code for the (should-never-happen-in-practice, but defensive)
# case where a tracker record in status "discovered" has no matching Job
# snapshot to evaluate - a tracker/data-integrity concern, not one of
# T4.1's config-driven hard filters, so it lives here rather than in
# src/selection/filters.py's stable filter-reason set.
REASON_MISSING_JOB_DETAILS = "missing_job_details"


@dataclass
class SelectionRunResult:
    """Summary of one `select_and_queue()` call - also what gets logged as
    the `selection_run_summary` event."""

    jobs_considered: int = 0
    jobs_selected: int = 0
    jobs_skipped: int = 0
    skip_reason_counts: dict[str, int] = field(default_factory=dict)
    ticket_id: str | None = None


def _record_skip(tracker, job_id: str, reason: str, result: SelectionRunResult) -> None:
    tracker.transition(job_id, "skipped", meta={"skip_reason": reason})
    result.jobs_skipped += 1
    result.skip_reason_counts[reason] = result.skip_reason_counts.get(reason, 0) + 1


def _selected_today_count(tracker, *, now_fn: Callable[[], datetime]) -> int:
    """S4.3.1's "minus already-submitted-today": counted here as every
    tracker record whose `timestamps["selected"]` date matches `now_fn()`'s
    date, REGARDLESS of the record's *current* status.

    Deliberately scans `tracker.get_jobs()` (all records) rather than
    `tracker.get_jobs(status="selected")`: a job selected earlier today may
    have since progressed past `selected` (`resume_tailored` -> `applying`
    -> `submitted`, or even `failed`/`skipped` further down the pipeline),
    but it already consumed today's quota the moment it was selected - the
    state machine (spec §3.2) never clears `timestamps["selected"]` on
    later transitions, so this is the reliable ledger of "already claimed
    today's budget", not just "currently sitting in status=selected".
    """
    today = now_fn().date()
    count = 0
    for record in tracker.get_jobs():
        selected_at = record.timestamps.get("selected")
        if selected_at is not None and selected_at.date() == today:
            count += 1
    return count


def select_and_queue(
    tracker,
    settings: Settings,
    *,
    skills: list[str],
    run_id: str,
    logger=None,
    now_fn: Callable[[], datetime] = lambda: datetime.now(UTC),
    rationale_fn: Callable[[Job], str] | None = None,
) -> SelectionRunResult:
    """Run one full selection pass (spec EPIC 4):

    1. Pull every `discovered` job from the tracker.
    2. Apply T4.1's hard filters; a failing job is transitioned to
       `skipped` with its reason code recorded (`skip_reason` meta key).
    3. Score + rank the survivors (T4.2), deterministically.
    4. Compute today's remaining quota (`max_applications_per_day` minus
       jobs already `selected` today) and transition the top-`quota`
       survivors to `selected`; the rest are left in `discovered` for
       re-evaluation on a future run (S4.3.2 - never force-skipped just
       for missing this run's quota cut).
    5. If `settings.approval_mode != "auto"` and at least one job was
       selected, open exactly one `HITLTicket(kind="batch_approval")`
       covering the whole batch (S4.3.3).

    `now_fn` is called exactly ONCE, up front, and that single frozen
    instant is reused for every hard-filter/scoring/quota decision in this
    run - not re-called per job. A real wall-clock `now_fn` called fresh
    per job (of which there may be hundreds) would let "now" drift over
    the run's own execution time, undermining the spec DoD's "selection
    is deterministic" for a fixed (jobs, settings) input (see the matching
    note on `src/selection/scoring.py:rank_jobs()`, which independently
    freezes its own `now_fn` the same way for callers that use it
    directly rather than through this function).
    """
    result = SelectionRunResult()
    frozen_now = now_fn()

    def _frozen_now_fn() -> datetime:
        return frozen_now

    candidates = tracker.get_jobs(status="discovered")
    result.jobs_considered = len(candidates)

    survivors: list[Job] = []
    for record in candidates:
        job = tracker.get_job_details(record.job_id)
        if job is None:
            _record_skip(tracker, record.job_id, REASON_MISSING_JOB_DETAILS, result)
            if logger is not None:
                log_event(
                    logger,
                    "job_hard_filter_skipped",
                    run_id=run_id,
                    job_id=record.job_id,
                    skip_reason=REASON_MISSING_JOB_DETAILS,
                )
            continue

        ok, reason = passes_hard_filters(job, settings, now_fn=_frozen_now_fn)
        if not ok:
            _record_skip(tracker, job.job_id, reason, result)
            if logger is not None:
                log_event(
                    logger,
                    "job_hard_filter_skipped",
                    run_id=run_id,
                    job_id=job.job_id,
                    skip_reason=reason,
                )
            continue

        survivors.append(job)

    ranked = rank_jobs(
        survivors, settings, skills, now_fn=_frozen_now_fn, rationale_fn=rationale_fn
    )

    already_selected_today = _selected_today_count(tracker, now_fn=_frozen_now_fn)
    quota = max(settings.max_applications_per_day - already_selected_today, 0)
    to_select = ranked[:quota]

    for scored in to_select:
        tracker.transition(scored.job_id, "selected")
        result.jobs_selected += 1
        if logger is not None:
            log_event(
                logger,
                "job_selected",
                run_id=run_id,
                job_id=scored.job_id,
                score=scored.score,
                rationale=scored.rationale,
            )

    ticket_id = None
    if settings.approval_mode != "auto" and result.jobs_selected > 0:
        selected_job_ids = [scored.job_id for scored in to_select]
        ticket = HITLTicket(
            ticket_id=f"batch-{run_id}-{uuid.uuid4().hex[:8]}",
            job_id=BATCH_SELECTION_JOB_ID,
            kind="batch_approval",
            context={"job_ids": selected_job_ids, "run_id": run_id},
        )
        tracker.add_ticket(ticket)
        ticket_id = ticket.ticket_id
        if logger is not None:
            log_event(
                logger,
                "batch_approval_ticket_opened",
                run_id=run_id,
                ticket_id=ticket_id,
                job_ids=selected_job_ids,
            )

    result.ticket_id = ticket_id

    if logger is not None:
        log_event(
            logger,
            "selection_run_summary",
            run_id=run_id,
            jobs_considered=result.jobs_considered,
            jobs_selected=result.jobs_selected,
            jobs_skipped=result.jobs_skipped,
            skip_reason_counts=result.skip_reason_counts,
            ticket_id=result.ticket_id,
        )

    return result
