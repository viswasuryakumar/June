"""Tests for src/selection/engine.py (spec EPIC 4 orchestration):
select_and_queue() respecting quota, being deterministic given a fixture
set, every skip carrying a machine-readable reason, and the batch HITL
approval ticket only firing when approval_mode != "auto".

Pure logic - no browser - built against InMemoryTrackerRepository, mirroring
tests/test_discovery_sync.py's fixture style but without any Playwright
dependency at all (Selection never touches a page).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from src.config.settings import Settings
from src.contracts.models import Job
from src.selection.engine import BATCH_SELECTION_JOB_ID, select_and_queue
from src.tracker.repository import InMemoryTrackerRepository

FIXED_NOW = datetime(2026, 7, 11, 12, 0, tzinfo=UTC)


def _now_fn() -> datetime:
    return FIXED_NOW


def _job(job_id: str, **overrides) -> Job:
    defaults = dict(
        job_id=job_id,
        title="Backend Engineer",
        company="Acme",
        location="Remote",
        remote_type="remote",
        salary_min=110000,
        salary_max=140000,
        match_score=80,
        posted_at=FIXED_NOW,
        jobright_url=f"https://jobright.ai/jobs/{job_id}",
        raw_description="Python and SQL role.",
    )
    defaults.update(overrides)
    return Job(**defaults)


def _settings(**overrides) -> Settings:
    defaults = dict(
        max_applications_per_day=2,
        min_match_score=70,
        salary_floor=100000,
        max_posting_age_days=30,
        approval_mode="approve_each",
    )
    defaults.update(overrides)
    return Settings(**defaults)


def _tracker_with_jobs(*jobs: Job) -> InMemoryTrackerRepository:
    tracker = InMemoryTrackerRepository()
    for job in jobs:
        tracker.add_job(job)
    return tracker


def test_select_and_queue_respects_quota():
    jobs = [_job(f"jr-{i}", match_score=90 - i) for i in range(5)]
    tracker = _tracker_with_jobs(*jobs)
    settings = _settings(max_applications_per_day=2)

    result = select_and_queue(tracker, settings, skills=["Python"], run_id="run-1", now_fn=_now_fn)

    assert result.jobs_considered == 5
    assert result.jobs_selected == 2
    selected = tracker.get_jobs(status="selected")
    assert len(selected) == 2
    # Top 2 by match_score (jr-0=90, jr-1=89) should be the ones selected.
    assert sorted(r.job_id for r in selected) == ["jr-0", "jr-1"]
    # The rest remain "discovered" for re-evaluation next run.
    assert len(tracker.get_jobs(status="discovered")) == 3


def test_select_and_queue_is_deterministic():
    def build_tracker():
        jobs = [_job(f"jr-{i}", match_score=70 + i) for i in range(6)]
        return _tracker_with_jobs(*jobs)

    settings = _settings(max_applications_per_day=3)
    tracker1 = build_tracker()
    tracker2 = build_tracker()

    result1 = select_and_queue(
        tracker1, settings, skills=["Python"], run_id="run-a", now_fn=_now_fn
    )
    result2 = select_and_queue(
        tracker2, settings, skills=["Python"], run_id="run-b", now_fn=_now_fn
    )

    selected1 = sorted(r.job_id for r in tracker1.get_jobs(status="selected"))
    selected2 = sorted(r.job_id for r in tracker2.get_jobs(status="selected"))
    assert selected1 == selected2
    assert result1.jobs_selected == result2.jobs_selected == 3


def test_every_skip_has_a_machine_readable_reason():
    jobs = [
        _job("jr-lowscore", match_score=10),
        _job("jr-blocked", company="BadCo"),
        _job("jr-ok", match_score=95),
    ]
    tracker = _tracker_with_jobs(*jobs)
    settings = _settings(max_applications_per_day=5, blocklisted_companies=["BadCo"])

    result = select_and_queue(tracker, settings, skills=[], run_id="run-1", now_fn=_now_fn)

    assert result.jobs_skipped == 2
    skipped_records = {r.job_id: r for r in tracker.get_jobs(status="skipped")}
    assert set(skipped_records) == {"jr-lowscore", "jr-blocked"}
    assert skipped_records["jr-lowscore"].skip_reason == "below_min_match_score"
    assert skipped_records["jr-blocked"].skip_reason == "blocklisted_company"
    assert result.skip_reason_counts == {
        "below_min_match_score": 1,
        "blocklisted_company": 1,
    }
    # The survivor is selected, not left dangling.
    assert tracker.get_job("jr-ok").status == "selected"


def test_survivors_beyond_quota_stay_discovered_not_skipped():
    jobs = [_job(f"jr-{i}", match_score=90) for i in range(4)]
    tracker = _tracker_with_jobs(*jobs)
    settings = _settings(max_applications_per_day=1)

    result = select_and_queue(tracker, settings, skills=[], run_id="run-1", now_fn=_now_fn)

    assert result.jobs_selected == 1
    assert result.jobs_skipped == 0  # over-quota survivors are NOT "skipped"
    assert len(tracker.get_jobs(status="discovered")) == 3


def test_quota_accounts_for_jobs_already_selected_today():
    jobs = [_job(f"jr-{i}", match_score=90) for i in range(3)]
    tracker = _tracker_with_jobs(*jobs)
    settings = _settings(max_applications_per_day=2)

    # Pre-seed one job as already selected "today" (real transition() uses
    # real wall-clock time internally, which falls on the same calendar
    # day as FIXED_NOW in this environment).
    tracker.transition("jr-0", "selected")

    result = select_and_queue(tracker, settings, skills=[], run_id="run-1", now_fn=_now_fn)

    # Quota was 2, 1 already used today -> only 1 more should be selected.
    assert result.jobs_selected == 1
    assert len(tracker.get_jobs(status="selected")) == 2


def test_quota_ignores_jobs_selected_on_a_previous_day():
    jobs = [_job(f"jr-{i}", match_score=90) for i in range(3)]
    tracker = _tracker_with_jobs(*jobs)
    settings = _settings(max_applications_per_day=2)

    tracker.transition("jr-0", "selected")
    # Whitebox-rewrite the timestamp to yesterday to simulate a prior day's
    # selection not counting against today's quota.
    record = tracker.get_job("jr-0")
    record.timestamps["selected"] = FIXED_NOW - timedelta(days=1)

    result = select_and_queue(tracker, settings, skills=[], run_id="run-1", now_fn=_now_fn)

    assert result.jobs_selected == 2
    assert len(tracker.get_jobs(status="selected")) == 3


def test_quota_counts_jobs_that_progressed_past_selected_today():
    # A job selected earlier today and since progressed all the way to
    # "submitted" must still consume today's quota - the ledger is
    # timestamps["selected"]'s date, not the record's *current* status.
    jobs = [_job(f"jr-{i}", match_score=90) for i in range(3)]
    tracker = _tracker_with_jobs(*jobs)
    settings = _settings(max_applications_per_day=2)

    tracker.transition("jr-0", "selected")
    tracker.transition("jr-0", "resume_tailored")
    tracker.transition("jr-0", "applying")
    tracker.transition("jr-0", "submitted")

    result = select_and_queue(tracker, settings, skills=[], run_id="run-1", now_fn=_now_fn)

    assert result.jobs_selected == 1
    assert tracker.get_job("jr-0").status == "submitted"  # untouched


def test_zero_quota_selects_nothing_but_still_applies_hard_filters():
    jobs = [_job("jr-0", match_score=95), _job("jr-1", match_score=5)]
    tracker = _tracker_with_jobs(*jobs)
    settings = _settings(max_applications_per_day=1)
    tracker.transition("jr-0", "selected")  # already uses up today's only slot

    result = select_and_queue(tracker, settings, skills=[], run_id="run-1", now_fn=_now_fn)

    assert result.jobs_selected == 0
    assert result.jobs_skipped == 1  # jr-1 still fails the hard filter
    assert tracker.get_job("jr-1").skip_reason == "below_min_match_score"


def test_batch_hitl_ticket_created_when_approval_mode_not_auto():
    jobs = [_job("jr-0", match_score=95), _job("jr-1", match_score=90)]
    tracker = _tracker_with_jobs(*jobs)
    settings = _settings(max_applications_per_day=5, approval_mode="approve_batch")

    result = select_and_queue(tracker, settings, skills=[], run_id="run-1", now_fn=_now_fn)

    assert result.ticket_id is not None
    tickets = tracker.list_tickets()
    assert len(tickets) == 1
    ticket = tickets[0]
    assert ticket.kind == "batch_approval"
    assert ticket.job_id == BATCH_SELECTION_JOB_ID
    assert sorted(ticket.context["job_ids"]) == ["jr-0", "jr-1"]
    assert ticket.context["run_id"] == "run-1"


def test_no_batch_hitl_ticket_when_approval_mode_is_auto():
    jobs = [_job("jr-0", match_score=95)]
    tracker = _tracker_with_jobs(*jobs)
    settings = _settings(max_applications_per_day=5, approval_mode="auto")

    result = select_and_queue(tracker, settings, skills=[], run_id="run-1", now_fn=_now_fn)

    assert result.ticket_id is None
    assert tracker.list_tickets() == []


def test_no_batch_hitl_ticket_when_nothing_was_selected():
    jobs = [_job("jr-0", match_score=5)]  # fails hard filter, nothing selected
    tracker = _tracker_with_jobs(*jobs)
    settings = _settings(max_applications_per_day=5, approval_mode="approve_batch")

    result = select_and_queue(tracker, settings, skills=[], run_id="run-1", now_fn=_now_fn)

    assert result.jobs_selected == 0
    assert result.ticket_id is None
    assert tracker.list_tickets() == []


def test_select_and_queue_works_without_rationale_fn():
    jobs = [_job("jr-0", match_score=95)]
    tracker = _tracker_with_jobs(*jobs)
    settings = _settings(max_applications_per_day=5, enable_llm_rationale=True)

    result = select_and_queue(
        tracker, settings, skills=[], run_id="run-1", now_fn=_now_fn, rationale_fn=None
    )

    assert result.jobs_selected == 1


def test_select_and_queue_invokes_rationale_fn_only_for_top_k_when_enabled():
    jobs = [_job(f"jr-{i}", match_score=90 - i) for i in range(4)]
    tracker = _tracker_with_jobs(*jobs)
    settings = _settings(
        max_applications_per_day=4, enable_llm_rationale=True, llm_rationale_top_k=2
    )
    calls: list[str] = []

    def rationale_fn(job: Job) -> str:
        calls.append(job.job_id)
        return "why this fits"

    result = select_and_queue(
        tracker,
        settings,
        skills=[],
        run_id="run-1",
        now_fn=_now_fn,
        rationale_fn=rationale_fn,
    )

    assert result.jobs_selected == 4
    assert sorted(calls) == ["jr-0", "jr-1"]


def test_missing_job_details_is_skipped_with_reason_not_crashed():
    tracker = InMemoryTrackerRepository()
    tracker.add_job(_job("jr-0", match_score=95))
    # Simulate a data-integrity gap: a "discovered" record with no
    # underlying Job snapshot (should not happen via add_job(), but the
    # engine must not crash if it ever does).
    del tracker._jobs["jr-0"]
    settings = _settings(max_applications_per_day=5)

    result = select_and_queue(tracker, settings, skills=[], run_id="run-1", now_fn=_now_fn)

    assert result.jobs_selected == 0
    assert result.jobs_skipped == 1
    assert tracker.get_job("jr-0").skip_reason == "missing_job_details"
