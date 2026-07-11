"""Deduplication, freshness filtering, and status-skip checks (spec T3.3).

`TrackerRepository.add_job()` already dedupes on exact `job_id`
(idempotent create, per its own docstring - see src/tracker/repository.py)
so this module deliberately does NOT reimplement that. What it adds is
the *secondary* fuzzy dedupe (S3.3.1) the tracker has no notion of:
catching reposts of the same real-world job under a brand-new job_id.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from src.contracts.models import ApplicationRecord, Job

NormalizedKey = tuple[str, str, str]


def normalize_title_company_location(company: str, title: str, location: str) -> NormalizedKey:
    """Documented assumption for the S3.3.1 fuzzy-dedupe key: casefold +
    collapse whitespace on each of (company, title, location), with no
    stemming/synonym/edit-distance matching. This is meant to catch exact
    reposts (identical text, new job_id) - true fuzzy/semantic matching
    (e.g. "Sr. Backend Eng" vs "Senior Backend Engineer") is out of scope
    here; flagged as a possible future improvement, not attempted, to keep
    this module small per the task's own "don't over-engineer" guidance.
    """

    def norm(value: str) -> str:
        return re.sub(r"\s+", " ", value.strip()).casefold()

    return norm(company), norm(title), norm(location)


def fuzzy_key(job: Job) -> NormalizedKey:
    return normalize_title_company_location(job.company, job.title, job.location)


def is_stale(
    job: Job,
    max_posting_age_days: int,
    *,
    now_fn: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> bool:
    """S3.3.3 freshness policy against `settings.max_posting_age_days`.

    Best-effort per the task brief: if `posted_at` could not be extracted
    (None), the job is NOT dropped just because its date is unknown -
    only a *positively known* stale `posted_at` causes a drop.
    """
    if job.posted_at is None:
        return False
    posted_at = job.posted_at
    if posted_at.tzinfo is None:
        posted_at = posted_at.replace(tzinfo=UTC)
    return (now_fn() - posted_at) > timedelta(days=max_posting_age_days)


def should_skip_existing(record: ApplicationRecord | None) -> bool:
    """S3.3.2: skip anything already in a terminal (`submitted`/`failed`/
    `skipped`) or in-flight (anything past `discovered`) state.

    Since the state machine (spec §3.2) only ever moves forward from
    `discovered` (with `needs_human <-> applying` the sole cycle), any
    status other than `discovered` - and, naturally, `None` for a
    never-before-seen job_id is not "existing" at all - means the job is
    already progressing or done, so re-upserting/re-enriching it would be
    redundant at best and could churn the tracker mid-flight at worst.
    """
    if record is None:
        return False
    return record.status != "discovered"


def find_fuzzy_duplicate(tracker, job: Job, *, exclude_job_id: str | None = None) -> str | None:
    """Scan the tracker's already-known jobs for one whose (company,
    normalized title, location) matches `job`'s but under a different
    job_id - i.e. a likely repost (S3.3.1). Returns the matching job_id,
    or None if no match is found.

    Deliberately built only against the existing `TrackerRepository`
    interface (`get_jobs()` + `get_job_details()`) rather than adding a
    new lookup method to the tracker itself - Epic 3 doesn't own
    tracker/repository.py and the task brief says to rely on, not
    reimplement or extend, its existing surface.
    """
    target_key = fuzzy_key(job)
    for record in tracker.get_jobs():
        if record.job_id == job.job_id or record.job_id == exclude_job_id:
            continue
        existing = tracker.get_job_details(record.job_id)
        if existing is None:
            continue
        if fuzzy_key(existing) == target_key:
            return record.job_id
    return None
