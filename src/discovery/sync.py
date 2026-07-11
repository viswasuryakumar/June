"""Top-level discovery orchestration (spec EPIC 3).

`sync_jobs()` ties together T3.1 (feed navigation + pagination), T3.2
(card extraction + detail enrichment), and T3.3 (dedup + persistence)
into the single entrypoint other epics/the orchestrator should call.

Dependency-injectable end to end (page, registry, tracker, settings,
run_id, logger) per the same pattern Epic 2's `src/auth/` package
established - no globals.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from src.discovery.dedupe import find_fuzzy_duplicate, is_stale, should_skip_existing
from src.discovery.extraction import (
    ExtractionStats,
    enrich_job_detail,
    extract_job_card,
    log_extraction_stats,
)
from src.discovery.feed import navigate_to_jobs_feed, scroll_and_collect_cards
from src.observability.logging import log_event

DEFAULT_JOBRIGHT_BASE_URL = "https://jobright.ai"


@dataclass
class DiscoveryRunResult:
    """Summary of one `sync_jobs()` call - also what gets logged as the
    end-of-run event (S3.2.3's "log field-level extraction stats per
    run" plus the T3.3 dedupe/freshness counts).

    `jobs_ingested` and `jobs_refreshed` are deliberately kept as two
    separate counters rather than folded together: `jobs_ingested` only
    counts job_ids the tracker had never seen before (a brand-new
    `ApplicationRecord` gets created in status `discovered`), while
    `jobs_refreshed` counts job_ids that were already tracked in status
    `discovered` from an earlier run and are merely having their stored
    `Job` snapshot (salary, match score, etc.) refreshed via
    `tracker.add_job()` - which is a no-op for the `ApplicationRecord`/
    status itself. Without this split, running discovery twice
    back-to-back over an unchanged feed would report the same
    `jobs_ingested` count both times even though no new tracker rows
    were created, which would mislead anyone reading run logs/reports
    into thinking new jobs were found on the second run too.
    """

    jobs_seen: int = 0
    jobs_ingested: int = 0
    jobs_refreshed: int = 0
    """Job_ids already tracked in status `discovered` from a previous
    run whose stored `Job` snapshot was refreshed this run, as distinct
    from `jobs_ingested`'s genuinely-new job_ids."""
    jobs_skipped_existing: int = 0
    jobs_skipped_fuzzy_duplicate: int = 0
    jobs_skipped_stale: int = 0
    jobs_enriched: int = 0
    pages_scrolled: int = 0
    terminated_reason: str = ""
    extraction_stats: dict = field(default_factory=dict)


def sync_jobs(
    page,
    registry,
    tracker,
    settings,
    run_id: str,
    *,
    logger=None,
    base_url: str = DEFAULT_JOBRIGHT_BASE_URL,
    enrichment_score_threshold: int | None = None,
    max_pages: int | None = None,
) -> DiscoveryRunResult:
    """Run one full discovery pass: navigate to the feed, paginate,
    extract every card, dedupe/freshness-filter, enrich survivors above
    the coarse score threshold, and upsert into the tracker.

    `enrichment_score_threshold`/`max_pages` override the corresponding
    `settings` fields when passed explicitly (mainly for tests); by
    default both come from `settings` (`discovery_enrichment_score_threshold`,
    `max_discovery_pages` - both Epic 3 additions, see PROGRESS.md).
    """
    effective_max_pages = max_pages if max_pages is not None else settings.max_discovery_pages
    effective_threshold = (
        enrichment_score_threshold
        if enrichment_score_threshold is not None
        else settings.discovery_enrichment_score_threshold
    )

    navigate_to_jobs_feed(page, registry, run_id, logger=logger)
    pagination = scroll_and_collect_cards(
        page, registry, run_id, max_pages=effective_max_pages, logger=logger
    )

    result = DiscoveryRunResult(
        pages_scrolled=pagination.pages_scrolled,
        terminated_reason=pagination.terminated_reason,
    )
    stats = ExtractionStats()

    for index, card in enumerate(pagination.cards):
        result.jobs_seen += 1
        job = extract_job_card(card, registry, base_url=base_url, index=index, stats=stats)

        if should_skip_existing(tracker.get_job(job.job_id)):
            result.jobs_skipped_existing += 1
            if logger is not None:
                log_event(logger, "job_skipped_existing_status", run_id=run_id, job_id=job.job_id)
            continue

        duplicate_of = find_fuzzy_duplicate(tracker, job)
        if duplicate_of is not None:
            result.jobs_skipped_fuzzy_duplicate += 1
            if logger is not None:
                log_event(
                    logger,
                    "job_fuzzy_duplicate_skipped",
                    run_id=run_id,
                    job_id=job.job_id,
                    duplicate_of=duplicate_of,
                )
            continue

        if is_stale(job, settings.max_posting_age_days):
            result.jobs_skipped_stale += 1
            if logger is not None:
                log_event(
                    logger,
                    "job_stale_skipped",
                    run_id=run_id,
                    job_id=job.job_id,
                    posted_at=str(job.posted_at),
                )
            continue

        # S3.2.2: only enrich (open the detail page) for candidates above
        # the coarse score threshold. A missing match_score is treated as
        # "not positively known to qualify" and is NOT enriched, to avoid
        # the (potentially expensive/rate-limited) detail-page hop for
        # every card whose score simply failed to parse.
        if job.match_score is not None and job.match_score >= effective_threshold:
            detail_page = page.context.new_page()
            try:
                job = enrich_job_detail(detail_page, registry, job, run_id=run_id, logger=logger)
                result.jobs_enriched += 1
            finally:
                detail_page.close()

        # A job_id the tracker has never seen is genuinely new
        # (jobs_ingested); one already tracked in status `discovered`
        # from an earlier run reaches this point only to have its stored
        # `Job` snapshot refreshed (jobs_refreshed) - `should_skip_existing()`
        # above already filtered out anything past `discovered`, so "known
        # but not skipped" and "unknown" are the only two cases left here.
        if tracker.get_job(job.job_id) is None:
            result.jobs_ingested += 1
        else:
            result.jobs_refreshed += 1
        tracker.add_job(job)

    result.extraction_stats = stats.as_dict()
    log_extraction_stats(logger, run_id, stats)

    if logger is not None:
        log_event(
            logger,
            "discovery_run_summary",
            run_id=run_id,
            jobs_seen=result.jobs_seen,
            jobs_ingested=result.jobs_ingested,
            jobs_refreshed=result.jobs_refreshed,
            jobs_skipped_existing=result.jobs_skipped_existing,
            jobs_skipped_fuzzy_duplicate=result.jobs_skipped_fuzzy_duplicate,
            jobs_skipped_stale=result.jobs_skipped_stale,
            jobs_enriched=result.jobs_enriched,
            pages_scrolled=result.pages_scrolled,
            terminated_reason=result.terminated_reason,
        )

    return result
