"""Tests for src/discovery/dedupe.py (spec T3.3): fuzzy-dedupe key
normalization, cross-job_id repost detection, freshness filtering, and
the tracker-status skip check.

Pure logic against `Job`/`InMemoryTrackerRepository` - no browser needed.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from src.contracts.models import Job
from src.discovery.dedupe import (
    find_fuzzy_duplicate,
    fuzzy_key,
    is_stale,
    normalize_title_company_location,
    should_skip_existing,
)
from src.tracker.repository import InMemoryTrackerRepository


def make_job(
    job_id: str,
    company: str = "Acme Corp",
    title: str = "Backend Engineer",
    location: str = "Remote - USA",
    posted_at=None,
) -> Job:
    return Job(
        job_id=job_id,
        title=title,
        company=company,
        location=location,
        jobright_url=f"https://jobright.ai/jobs/{job_id}",
        posted_at=posted_at,
    )


# -- normalize_title_company_location / fuzzy_key ----------------------------


def test_normalize_collapses_whitespace_and_casefolds():
    assert normalize_title_company_location(
        "  Acme   Corp ", "Backend  Engineer", "REMOTE - usa"
    ) == ("acme corp", "backend engineer", "remote - usa")


def test_fuzzy_key_matches_normalize_title_company_location():
    job = make_job("jid-1")
    assert fuzzy_key(job) == normalize_title_company_location(job.company, job.title, job.location)


# -- find_fuzzy_duplicate ------------------------------------------------------


def test_find_fuzzy_duplicate_detects_repost_under_different_job_id():
    tracker = InMemoryTrackerRepository()
    tracker.add_job(
        make_job(
            "jid-original", company="Acme Corp", title="Backend Engineer", location="Remote - USA"
        )
    )

    repost = make_job(
        "jid-repost",
        company="  ACME corp ",
        title="backend   engineer",
        location="REMOTE - usa",
    )
    assert find_fuzzy_duplicate(tracker, repost) == "jid-original"


def test_find_fuzzy_duplicate_returns_none_for_genuinely_different_jobs():
    tracker = InMemoryTrackerRepository()
    tracker.add_job(
        make_job(
            "jid-original", company="Acme Corp", title="Backend Engineer", location="Remote - USA"
        )
    )

    different_company = make_job(
        "jid-2", company="Beta LLC", title="Backend Engineer", location="Remote - USA"
    )
    different_title = make_job(
        "jid-3", company="Acme Corp", title="Frontend Engineer", location="Remote - USA"
    )
    different_location = make_job(
        "jid-4", company="Acme Corp", title="Backend Engineer", location="Onsite - NYC"
    )

    assert find_fuzzy_duplicate(tracker, different_company) is None
    assert find_fuzzy_duplicate(tracker, different_title) is None
    assert find_fuzzy_duplicate(tracker, different_location) is None


def test_find_fuzzy_duplicate_excludes_the_job_itself():
    tracker = InMemoryTrackerRepository()
    job = make_job("jid-original")
    tracker.add_job(job)
    assert find_fuzzy_duplicate(tracker, job) is None


def test_find_fuzzy_duplicate_scans_a_larger_tracker():
    tracker = InMemoryTrackerRepository()
    for i in range(100):
        tracker.add_job(make_job(f"jid-unrelated-{i}", company=f"Company {i}", title=f"Role {i}"))
    tracker.add_job(
        make_job("jid-real", company="Acme Corp", title="Backend Engineer", location="Remote - USA")
    )

    repost = make_job(
        "jid-real-repost",
        company="acme corp",
        title="backend engineer",
        location="remote - usa",
    )
    assert find_fuzzy_duplicate(tracker, repost) == "jid-real"

    not_a_match = make_job("jid-unmatched", company="Nobody Inc", title="Nothing")
    assert find_fuzzy_duplicate(tracker, not_a_match) is None


# -- is_stale ------------------------------------------------------------------


def test_is_stale_false_when_posted_recently():
    now = datetime(2026, 7, 11, tzinfo=UTC)
    job = make_job("jid-fresh", posted_at=now - timedelta(days=5))
    assert is_stale(job, max_posting_age_days=30, now_fn=lambda: now) is False


def test_is_stale_true_when_posted_before_cutoff():
    now = datetime(2026, 7, 11, tzinfo=UTC)
    job = make_job("jid-stale", posted_at=now - timedelta(days=40))
    assert is_stale(job, max_posting_age_days=30, now_fn=lambda: now) is True


def test_is_stale_exactly_at_boundary_is_not_stale():
    now = datetime(2026, 7, 11, tzinfo=UTC)
    job = make_job("jid-boundary", posted_at=now - timedelta(days=30))
    # `> timedelta(days=max_posting_age_days)`, so exactly-at-boundary is
    # NOT stale (strictly greater-than, not greater-or-equal).
    assert is_stale(job, max_posting_age_days=30, now_fn=lambda: now) is False


def test_is_stale_false_when_posted_at_is_unknown():
    job = make_job("jid-unknown-date", posted_at=None)
    # Per the Code agent's stated design choice: a job with no extractable
    # posted_at is NOT dropped just for that reason - only a *positively
    # known* stale date causes a drop.
    assert is_stale(job, max_posting_age_days=30) is False


# -- should_skip_existing -------------------------------------------------------


def test_should_skip_existing_false_for_unknown_job():
    assert should_skip_existing(None) is False


def test_should_skip_existing_false_for_discovered_status():
    tracker = InMemoryTrackerRepository()
    tracker.add_job(make_job("jid-1"))
    record = tracker.get_job("jid-1")
    assert record.status == "discovered"
    assert should_skip_existing(record) is False


def test_should_skip_existing_true_for_terminal_statuses():
    chains = {
        "skipped": ["selected", "skipped"],
        "failed": ["selected", "resume_tailored", "applying", "failed"],
        "submitted": ["selected", "resume_tailored", "applying", "submitted"],
    }
    for terminal_status, chain in chains.items():
        tracker = InMemoryTrackerRepository()
        job_id = f"jid-{terminal_status}"
        tracker.add_job(make_job(job_id))
        for status in chain:
            tracker.transition(job_id, status)
        record = tracker.get_job(job_id)
        assert record.status == terminal_status
        assert should_skip_existing(record) is True


def test_should_skip_existing_true_for_inflight_status():
    tracker = InMemoryTrackerRepository()
    tracker.add_job(make_job("jid-inflight"))
    tracker.transition("jid-inflight", "selected")
    record = tracker.get_job("jid-inflight")
    assert should_skip_existing(record) is True
