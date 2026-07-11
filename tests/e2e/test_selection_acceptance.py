"""Cross-stage acceptance tests for tracker state consumed by Selection.

Owned by Codex (sol). These tests exercise public interfaces across runtime
stage boundaries and intentionally avoid implementation-level monkeypatching.
"""

from __future__ import annotations

from datetime import UTC, datetime

from src.config.settings import Settings
from src.contracts.models import Job
from src.selection import select_and_queue
from src.tracker.repository import InMemoryTrackerRepository


def _job(job_id: str, match_score: int) -> Job:
    return Job(
        job_id=job_id,
        title="Backend Engineer",
        company="Acme",
        location="Remote",
        remote_type="remote",
        salary_min=120_000,
        salary_max=150_000,
        match_score=match_score,
        posted_at=datetime.now(UTC),
        jobright_url=f"https://jobright.ai/jobs/{job_id}",
        raw_description="Python SQL backend role",
    )


def test_submitted_today_application_consumes_selection_quota() -> None:
    """S4.3.1 counts completed submissions, not only selected records."""
    tracker = InMemoryTrackerRepository()
    tracker.add_job(_job("already-submitted", 99))
    tracker.transition("already-submitted", "selected")
    tracker.transition("already-submitted", "resume_tailored")
    tracker.transition("already-submitted", "applying")
    tracker.transition("already-submitted", "submitted")

    tracker.add_job(_job("new-candidate", 95))
    settings = Settings(
        max_applications_per_day=1,
        min_match_score=70,
        salary_floor=100_000,
        approval_mode="auto",
    )

    result = select_and_queue(
        tracker,
        settings,
        skills=["Python", "SQL"],
        run_id="e2e-selection-quota",
        now_fn=lambda: datetime.now(UTC),
    )

    assert result.jobs_selected == 0
    assert tracker.get_job("new-candidate").status == "discovered"


def test_two_hundred_job_selection_is_deterministic_and_auditable() -> None:
    """Epic 4 DoD: 200 jobs, deterministic quota, reasons for every skip."""

    def run_once(run_id: str):
        tracker = InMemoryTrackerRepository()
        for index in range(200):
            score = 95 if index % 2 == 0 else 50
            tracker.add_job(_job(f"job-{index:03d}", score))

        result = select_and_queue(
            tracker,
            Settings(
                max_applications_per_day=10,
                min_match_score=70,
                salary_floor=100_000,
                approval_mode="auto",
            ),
            skills=["Python", "SQL"],
            run_id=run_id,
            now_fn=lambda: datetime.now(UTC),
        )
        selected = sorted(record.job_id for record in tracker.get_jobs(status="selected"))
        skipped = tracker.get_jobs(status="skipped")
        return result, selected, skipped

    first, first_selected, first_skipped = run_once("e2e-selection-200-a")
    second, second_selected, second_skipped = run_once("e2e-selection-200-b")

    assert first.jobs_considered == second.jobs_considered == 200
    assert first.jobs_selected == second.jobs_selected == 10
    assert first.jobs_skipped == second.jobs_skipped == 100
    assert first_selected == second_selected
    assert all(record.skip_reason == "below_min_match_score" for record in first_skipped)
    assert all(record.skip_reason == "below_min_match_score" for record in second_skipped)
