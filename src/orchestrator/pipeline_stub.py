"""Epic 1 scaffold pipeline runner.

This is NOT Epic 9's real orchestrator (async staged pipeline, pacing,
safety rails, etc. - see jobright-automation-spec.md §4 EPIC 9). It exists
only so Epic 1's Definition of Done ("`make run --dry-run` executes an
empty pipeline end-to-end with fake data") is satisfiable today, and so
every other epic has a concrete place to plug a real stage implementation
in once it lands. Epic 9's owner should replace this module entirely.

Each stage below is a no-op stub that logs a structured "skipped" event
against the in-memory tracker fake and returns immediately - there is no
real job data yet (Epics 2/3 aren't built), so the pipeline runs "empty".
"""

from __future__ import annotations

import logging

from src.config.profile import Profile
from src.config.settings import Settings
from src.observability.logging import log_event
from src.tracker.repository import InMemoryTrackerRepository

STAGES = (
    "auth",
    "discovery",
    "selection",
    "resume_tailoring",
    "executor",
    "reporting",
)


def run_empty_pipeline(
    *,
    run_id: str,
    logger: logging.Logger,
    settings: Settings,
    profile: Profile,
    tracker: InMemoryTrackerRepository,
    dry_run: bool,
    stages: tuple[str, ...] = STAGES,
) -> dict:
    """Run each pipeline stage as a logged no-op stub against fake data.

    Returns a run summary dict (also what `make run --dry-run` prints).
    """
    log_event(
        logger,
        "pipeline_start",
        run_id=run_id,
        dry_run=dry_run,
        max_applications_per_day=settings.max_applications_per_day,
        approval_mode=settings.approval_mode,
    )

    completed_stages = []
    for stage in stages:
        log_event(
            logger,
            "stage_skipped",
            stage=stage,
            reason="not_yet_implemented",
            dry_run=dry_run,
        )
        completed_stages.append(stage)

    counts = {
        status: len(tracker.get_jobs(status))
        for status in (
            "discovered",
            "selected",
            "resume_tailored",
            "applying",
            "needs_human",
            "submitted",
            "failed",
            "skipped",
        )
    }

    summary = {
        "run_id": run_id,
        "dry_run": dry_run,
        "stages_run": completed_stages,
        "job_counts": counts,
        "profile_owner": profile.full_name or "(unset)",
    }
    log_event(logger, "pipeline_end", run_id=run_id, dry_run=dry_run, **{"job_counts": counts})
    return summary
