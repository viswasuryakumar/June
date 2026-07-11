"""Hard filters for the Selection Engine (spec EPIC 4, T4.1 / S4.1.1).

`passes_hard_filters()` is the single entrypoint: it returns `(True, None)`
if a job clears every configured hard filter, or `(False, reason_code)` on
the FIRST failing filter, checked in a fixed, documented order so the same
(job, settings) pair always produces the same reason code (spec's DoD:
"every skip has a machine-readable reason", deterministically).

Reuses `src.discovery.dedupe.is_stale()` for the posting-age check rather
than reimplementing Epic 3's freshness logic.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import UTC, datetime

from src.config.settings import LocationRules, Settings
from src.contracts.models import Job
from src.discovery.dedupe import is_stale

# Stable, documented reason codes (spec S4.3.2's "machine-readable reason").
# Treat these as part of the public contract: renaming one is a breaking
# change for anything reading ApplicationRecord.skip_reason downstream.
REASON_BELOW_MIN_MATCH_SCORE = "below_min_match_score"
REASON_TITLE_EXCLUDED = "title_excluded"
REASON_TITLE_NOT_INCLUDED = "title_not_included"
REASON_LOCATION_POLICY_VIOLATION = "location_policy_violation"
REASON_BELOW_SALARY_FLOOR = "below_salary_floor"
REASON_BLOCKLISTED_COMPANY = "blocklisted_company"
REASON_POSTING_TOO_OLD = "posting_too_old"

# Documented assumption for S4.1.1's "location/remote policy" rule: each
# policy is a strictly widening allow-list of `Job.remote_type` values,
# with "any" being the only policy that also tolerates "unknown" (i.e. a
# listing whose remote/onsite/hybrid status couldn't be determined at
# all during discovery).
_POLICY_ALLOWED_REMOTE_TYPES: dict[str, frozenset[str]] = {
    "remote_only": frozenset({"remote"}),
    "hybrid_ok": frozenset({"remote", "hybrid"}),
    "onsite_ok": frozenset({"remote", "hybrid", "onsite"}),
    "any": frozenset({"remote", "hybrid", "onsite", "unknown"}),
}


def _title_excluded(title: str, patterns: list[str]) -> bool:
    return any(re.search(pattern, title) for pattern in patterns)


def _title_included(title: str, patterns: list[str]) -> bool:
    # An empty include list means "no include restriction" - everything
    # passes this particular check (only the exclude list actively drops
    # jobs when include patterns aren't configured).
    if not patterns:
        return True
    return any(re.search(pattern, title) for pattern in patterns)


def _location_violates_policy(job: Job, rules: LocationRules) -> bool:
    allowed_remote_types = _POLICY_ALLOWED_REMOTE_TYPES[rules.remote_policy]
    if job.remote_type not in allowed_remote_types:
        return True

    location = job.location.casefold()
    if rules.disallowed_locations and any(
        loc.casefold() in location for loc in rules.disallowed_locations
    ):
        return True
    if rules.allowed_locations and not any(
        loc.casefold() in location for loc in rules.allowed_locations
    ):
        return True
    return False


def _below_salary_floor(job: Job, salary_floor: int | None, unknown_salary_policy: str) -> bool:
    if salary_floor is None:
        return False  # no floor configured - nothing to violate

    effective = job.salary_max if job.salary_max is not None else job.salary_min
    if effective is None:
        # Unknown salary: honor the configured policy rather than guessing.
        return unknown_salary_policy == "reject"
    return effective < salary_floor


def _is_blocklisted(company: str, blocklisted_companies: list[str]) -> bool:
    normalized = company.strip().casefold()
    return any(normalized == blocked.strip().casefold() for blocked in blocklisted_companies)


def passes_hard_filters(
    job: Job,
    settings: Settings,
    *,
    now_fn: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> tuple[bool, str | None]:
    """S4.1.1: apply every configured hard filter to `job` in a fixed
    order. Returns `(True, None)` if it survives all of them, or
    `(False, reason_code)` on the first one it fails.

    Order (deterministic, never reordered without a PROGRESS.md note since
    it's part of the observable reason-code contract): min match score,
    title exclude, title include, location/remote policy, salary floor,
    company blocklist, posting age.

    A `None` `match_score` is treated as "not positively known to meet the
    threshold" and fails the min-match-score check - the same fail-closed
    stance the discovery/dedupe freshness filter takes for an unparsable
    `posted_at`, just inverted (there, "unknown" means "don't drop"; here,
    an unscored job can't be confirmed to have cleared a stricter
    apply-worthiness bar, so it's treated conservatively).
    """
    if job.match_score is None or job.match_score < settings.min_match_score:
        return False, REASON_BELOW_MIN_MATCH_SCORE

    if _title_excluded(job.title, settings.title_exclude_regexes):
        return False, REASON_TITLE_EXCLUDED

    if not _title_included(job.title, settings.title_include_regexes):
        return False, REASON_TITLE_NOT_INCLUDED

    if _location_violates_policy(job, settings.location_rules):
        return False, REASON_LOCATION_POLICY_VIOLATION

    if _below_salary_floor(job, settings.salary_floor, settings.unknown_salary_policy):
        return False, REASON_BELOW_SALARY_FLOOR

    if _is_blocklisted(job.company, settings.blocklisted_companies):
        return False, REASON_BLOCKLISTED_COMPANY

    if is_stale(job, settings.max_posting_age_days, now_fn=now_fn):
        return False, REASON_POSTING_TOO_OLD

    return True, None
