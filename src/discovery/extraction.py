"""Job card extraction + detail enrichment (spec T3.2).

Every field extraction here is optional-with-default (S3.2.3): a missing
or malformed field never raises and never kills the run. This module
uses the non-raising `locator_present()`/best-effort read helpers for
everything - a missing card field (no salary shown, no posted-time text,
etc.) is a completely normal per-listing variation, not a broken
selector. Genuine structural breaks (the whole feed container missing)
are handled one layer up in `src/discovery/feed.py` via the raising
`resolve_locator()`.

No live jobright.ai access exists in this environment - built/testable
against local fixture HTML (see the Code agent's scratch smoke script,
not committed to tests/) rather than the real site.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from urllib.parse import urljoin, urlparse

from src.auth.context import locator_present
from src.contracts.models import ApplyMode, Job, RemoteType
from src.observability.logging import log_event
from src.observability.selectors import SelectorRegistry

DEFAULT_FIELD_TIMEOUT_MS = 500
DEFAULT_DETAIL_FIELD_TIMEOUT_MS = 1000

_RELATIVE_TIME_RE = re.compile(r"(?i)(\d+)\s*(minute|hour|day|week|month)s?\s*ago")
_SALARY_NUMBER_RE = re.compile(r"([\d,]+(?:\.\d+)?)\s*([kK])?")
_SALARY_SPLIT_RE = re.compile(r"-|–|—|\bto\b", re.IGNORECASE)

_UNIT_SECONDS = {
    "minute": 60,
    "hour": 3600,
    "day": 86400,
    "week": 604800,
    "month": 2592000,  # 30-day approximation; freshness policy only needs day-granularity
}


@dataclass
class ExtractionStats:
    """Per-run field-level extraction stats (S3.2.3: "log field-level
    extraction stats per run"). Counts of cards for which a given field
    could NOT be extracted and fell back to its default.
    """

    cards_seen: int = 0
    job_id_from_href: int = 0
    job_id_synthetic: int = 0
    missing_title: int = 0
    missing_company: int = 0
    missing_location: int = 0
    missing_salary_min: int = 0
    missing_salary_max: int = 0
    missing_match_score: int = 0
    missing_posted_at: int = 0

    def as_dict(self) -> dict:
        return asdict(self)


# -- card-level field readers (best-effort, never raise) --------------------


def _card_text(card, registry: SelectorRegistry, key: str, *, default: str = "") -> str:
    try:
        selector = registry.get(key)
    except KeyError:
        return default
    try:
        loc = card.locator(selector).first
        if loc.count() == 0:
            return default
        text = loc.inner_text(timeout=DEFAULT_FIELD_TIMEOUT_MS).strip()
        return text or default
    except Exception:
        return default


def _card_href(card, registry: SelectorRegistry, key: str) -> str | None:
    try:
        selector = registry.get(key)
    except KeyError:
        return None
    try:
        loc = card.locator(selector).first
        if loc.count() == 0:
            return None
        href = loc.get_attribute("href", timeout=DEFAULT_FIELD_TIMEOUT_MS)
        return href or None
    except Exception:
        return None


def derive_job_id(href: str | None, fallback_seed: str) -> tuple[str, bool]:
    """Documented assumption (no live DOM to verify against): JobRight job
    URLs end in a path like `/jobs/<job_id>` - see the existing fixtures
    in tests/test_contracts.py / tests/test_tracker_repository.py, both of
    which use `https://jobright.ai/jobs/<job_id>`. job_id is therefore
    parsed as the last non-empty path segment of the card's link href
    (query string stripped first, trailing slash stripped).

    If no usable href can be read at all (missing selector, absent
    element, or an href with no path segment), a stable synthetic id is
    derived instead by hashing `fallback_seed` (expected to be something
    like "company|title|location|index") - job_id is a *required* field
    on the `Job` model with no default, so it must NEVER be omitted, even
    when the DOM gives us nothing to key off. The `unknown-` prefix makes
    a synthetic id obvious to anyone reading logs/tracker rows later.

    Returns (job_id, extracted_from_href).
    """
    if href:
        path = urlparse(href).path.rstrip("/")
        segment = path.rsplit("/", 1)[-1] if path else ""
        if segment:
            return segment, True

    digest = hashlib.sha1(fallback_seed.encode("utf-8", errors="ignore")).hexdigest()[:16]
    return f"unknown-{digest}", False


def derive_jobright_url(href: str | None, base_url: str, job_id: str) -> str:
    """`jobright_url` is also required with no default. If a real href was
    found, resolve it against `base_url` (handles both absolute and
    site-relative hrefs); otherwise fall back to a synthesized
    `<base_url>/jobs/<job_id>` URL so the field is never empty.
    """
    if href:
        return urljoin(base_url, href)
    return urljoin(base_url.rstrip("/") + "/", f"jobs/{job_id}")


def _parse_salary_component(token: str) -> int | None:
    match = _SALARY_NUMBER_RE.search(token)
    if not match:
        return None
    try:
        value = float(match.group(1).replace(",", ""))
    except ValueError:
        return None
    if match.group(2):
        value *= 1000
    return int(value)


def _extract_salary(card, registry: SelectorRegistry) -> tuple[int | None, int | None]:
    text = _card_text(card, registry, "jobs.salary", default="")
    if not text:
        return None, None
    parts = _SALARY_SPLIT_RE.split(text, maxsplit=1)
    if len(parts) >= 2:
        return _parse_salary_component(parts[0]), _parse_salary_component(parts[1])
    value = _parse_salary_component(text)
    return value, value


def _extract_match_score(card, registry: SelectorRegistry) -> int | None:
    text = _card_text(card, registry, "jobs.match_score", default="")
    if not text:
        return None
    match = re.search(r"\d+", text)
    return int(match.group()) if match else None


def _extract_posted_at(
    card,
    registry: SelectorRegistry,
    *,
    now_fn: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> datetime | None:
    """Best-effort relative-time parsing ("3 days ago", "Posted today",
    "2 hours ago"). Anything else (absolute dates, unrecognized phrasing)
    degrades to None rather than raising or guessing - the freshness
    filter (src/discovery/dedupe.py:is_stale) treats an unparsable
    posted_at as "not positively known to be stale", never dropping a job
    on that basis alone.
    """
    text = _card_text(card, registry, "jobs.posted_at", default="")
    if not text:
        return None
    lowered = text.lower()
    if "today" in lowered or "just now" in lowered:
        return now_fn()
    match = _RELATIVE_TIME_RE.search(lowered)
    if not match:
        return None
    amount = int(match.group(1))
    unit_seconds = _UNIT_SECONDS[match.group(2)]
    return now_fn() - timedelta(seconds=amount * unit_seconds)


def _infer_remote_type(location_text: str) -> RemoteType:
    """No dedicated remote-type selector/field exists in the spec's card
    layout description - documented assumption: infer it from the
    location text itself (e.g. "Remote", "Hybrid - Austin, TX"), falling
    back to "onsite" when a concrete location is present with no remote/
    hybrid keyword, and "unknown" only when location itself is blank.
    """
    lowered = location_text.lower()
    if "remote" in lowered:
        return "remote"
    if "hybrid" in lowered:
        return "hybrid"
    if location_text:
        return "onsite"
    return "unknown"


def extract_job_card(
    card,
    registry: SelectorRegistry,
    *,
    base_url: str,
    index: int,
    stats: ExtractionStats,
) -> Job:
    """S3.2.1: parse one visible job card into a `Job` model instance.

    `card` is a Playwright Locator matching a single card element (one
    entry from `PaginationResult.cards`); every sub-field is queried
    relative to it. Every extraction step is independently best-effort -
    a malformed/missing field never prevents the rest of the card (or the
    run) from being processed; `stats` accumulates per-field miss counts
    for the caller to log once per run (S3.2.3).
    """
    stats.cards_seen += 1

    href = _card_href(card, registry, "jobs.card_link")

    title = _card_text(card, registry, "jobs.title", default="")
    if not title:
        stats.missing_title += 1

    company = _card_text(card, registry, "jobs.company", default="")
    if not company:
        stats.missing_company += 1

    # `location` is required on the Job model with no default - always
    # produce a concrete string (possibly "") rather than omitting it.
    location = _card_text(card, registry, "jobs.location", default="")
    if not location:
        stats.missing_location += 1

    fallback_seed = f"{company}|{title}|{location}|{index}"
    job_id, from_href = derive_job_id(href, fallback_seed)
    if from_href:
        stats.job_id_from_href += 1
    else:
        stats.job_id_synthetic += 1

    jobright_url = derive_jobright_url(href, base_url, job_id)

    salary_min, salary_max = _extract_salary(card, registry)
    if salary_min is None:
        stats.missing_salary_min += 1
    if salary_max is None:
        stats.missing_salary_max += 1

    match_score = _extract_match_score(card, registry)
    if match_score is None:
        stats.missing_match_score += 1

    posted_at = _extract_posted_at(card, registry)
    if posted_at is None:
        stats.missing_posted_at += 1

    return Job(
        job_id=job_id,
        title=title,
        company=company,
        location=location,
        remote_type=_infer_remote_type(location),
        salary_min=salary_min,
        salary_max=salary_max,
        match_score=match_score,
        posted_at=posted_at,
        jobright_url=jobright_url,
        external_url=None,
        apply_mode="unknown",
        raw_description="",
    )


def log_extraction_stats(logger, run_id: str, stats: ExtractionStats) -> None:
    """S3.2.3: log field-level extraction stats once per discovery run."""
    if logger is None:
        return
    log_event(logger, "job_extraction_stats", run_id=run_id, **stats.as_dict())


# -- detail-page enrichment (S3.2.2) -----------------------------------------


def _page_text(page, registry: SelectorRegistry, key: str, *, default: str = "") -> str:
    try:
        selector = registry.get(key)
    except KeyError:
        return default
    try:
        loc = page.locator(selector).first
        if loc.count() == 0:
            return default
        text = loc.inner_text(timeout=DEFAULT_DETAIL_FIELD_TIMEOUT_MS).strip()
        return text or default
    except Exception:
        return default


def _page_href(page, registry: SelectorRegistry, key: str) -> str | None:
    try:
        selector = registry.get(key)
    except KeyError:
        return None
    try:
        loc = page.locator(selector).first
        if loc.count() == 0:
            return None
        href = loc.get_attribute("href", timeout=DEFAULT_DETAIL_FIELD_TIMEOUT_MS)
        return href or None
    except Exception:
        return None


def _determine_apply_mode(
    page, registry: SelectorRegistry, external_url: str | None, *, run_id: str
) -> ApplyMode:
    """Which apply affordance JobRight shows on the detail page determines
    `apply_mode` (S3.2.2): "Apply with Agent" wins if present, else the
    extension autofill affordance, else - if we at least found an
    external posting link - `manual_only`; if none of that is present,
    `unknown` (never guess "agent"/"extension" without positive evidence).
    Reuses the existing `apply.agent_button` / `apply.extension_autofill_button`
    selector keys (already defined for Epic 6) rather than adding new ones.
    """
    if locator_present(page, registry, "apply.agent_button", run_id=run_id, timeout_ms=1000):
        return "agent"
    if locator_present(
        page, registry, "apply.extension_autofill_button", run_id=run_id, timeout_ms=1000
    ):
        return "extension"
    if external_url:
        return "manual_only"
    return "unknown"


def enrich_job_detail(
    detail_page,
    registry: SelectorRegistry,
    job: Job,
    *,
    run_id: str,
    timeout_ms: int = 8000,
    logger=None,
) -> Job:
    """S3.2.2: open the job's detail view and extract the full
    description, external apply URL, and apply_mode.

    `detail_page` is expected to be a fresh page opened via
    `page.context.new_page()` by the caller (see
    src/discovery/sync.py:sync_jobs) rather than the feed page itself -
    navigating the feed page away to a detail view would invalidate the
    already-collected card Locators mid-loop. The caller is responsible
    for closing `detail_page` afterwards.

    Every field here is best-effort via non-raising reads /
    `locator_present()` - a job with no description block or no external
    link (e.g. an Agent-only listing) is a normal variation, not a
    SelectorBroken. The navigation itself is also wrapped: a single job
    with a dead/unreachable jobright_url (network blip, job pulled
    between listing and enrichment, etc.) must not crash the whole
    discovery run - on a navigation failure this logs a warning and
    returns `job` unchanged (apply_mode stays whatever it already was,
    typically "unknown") rather than propagating the exception.
    """
    try:
        detail_page.goto(job.jobright_url, timeout=timeout_ms)
    except Exception as exc:
        if logger is not None:
            log_event(
                logger,
                "job_detail_navigation_failed",
                level=30,
                run_id=run_id,
                job_id=job.job_id,
                error=str(exc),
            )
        return job

    try:
        detail_page.wait_for_load_state("networkidle", timeout=timeout_ms)
    except Exception:
        pass

    description = _page_text(
        detail_page, registry, "jobs.detail_description", default=job.raw_description
    )
    external_url = (
        _page_href(detail_page, registry, "jobs.detail_external_link") or job.external_url
    )
    apply_mode = _determine_apply_mode(detail_page, registry, external_url, run_id=run_id)

    updated = job.model_copy(
        update={
            "raw_description": description,
            "external_url": external_url,
            "apply_mode": apply_mode,
        }
    )

    if logger is not None:
        log_event(
            logger,
            "job_detail_enriched",
            run_id=run_id,
            job_id=job.job_id,
            apply_mode=apply_mode,
            has_external_url=external_url is not None,
        )

    return updated
