"""Job Discovery & Sync (Epic 3 - spec §4 EPIC 3).

Public surface other epics (selection, orchestrator) should import from:

    from src.discovery import sync_jobs, DiscoveryRunResult

No live jobright.ai access exists in this environment (no credentials) -
every function here is built/testable against local fixtures (`data:`
URL pages simulating a jobs feed) rather than the real site. See
PROGRESS.md for the Epic 3 implementation entry and known gaps.
"""

from __future__ import annotations

from src.discovery.dedupe import (
    find_fuzzy_duplicate,
    fuzzy_key,
    is_stale,
    normalize_title_company_location,
    should_skip_existing,
)
from src.discovery.extraction import (
    ExtractionStats,
    derive_job_id,
    derive_jobright_url,
    enrich_job_detail,
    extract_job_card,
    log_extraction_stats,
)
from src.discovery.feed import (
    PaginationResult,
    navigate_to_jobs_feed,
    scroll_and_collect_cards,
)
from src.discovery.sync import DiscoveryRunResult, sync_jobs

__all__ = [
    "find_fuzzy_duplicate",
    "fuzzy_key",
    "is_stale",
    "normalize_title_company_location",
    "should_skip_existing",
    "ExtractionStats",
    "derive_job_id",
    "derive_jobright_url",
    "enrich_job_detail",
    "extract_job_card",
    "log_extraction_stats",
    "PaginationResult",
    "navigate_to_jobs_feed",
    "scroll_and_collect_cards",
    "DiscoveryRunResult",
    "sync_jobs",
]
