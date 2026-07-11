"""Selection Engine (Epic 4 - spec §4 EPIC 4).

Public surface other epics (resume tailoring, orchestrator) should import
from:

    from src.selection import select_and_queue, SelectionRunResult

Pure logic, no browser involved - built/testable entirely against
`InMemoryTrackerRepository` fixtures. See PROGRESS.md for the Epic 4
implementation entry and documented contract extensions.
"""

from __future__ import annotations

from src.selection.engine import (
    BATCH_SELECTION_JOB_ID,
    REASON_MISSING_JOB_DETAILS,
    SelectionRunResult,
    select_and_queue,
)
from src.selection.filters import (
    REASON_BELOW_MIN_MATCH_SCORE,
    REASON_BELOW_SALARY_FLOOR,
    REASON_BLOCKLISTED_COMPANY,
    REASON_LOCATION_POLICY_VIOLATION,
    REASON_POSTING_TOO_OLD,
    REASON_TITLE_EXCLUDED,
    REASON_TITLE_NOT_INCLUDED,
    passes_hard_filters,
)
from src.selection.scoring import ScoredJob, composite_score, rank_jobs

__all__ = [
    "BATCH_SELECTION_JOB_ID",
    "REASON_MISSING_JOB_DETAILS",
    "SelectionRunResult",
    "select_and_queue",
    "REASON_BELOW_MIN_MATCH_SCORE",
    "REASON_BELOW_SALARY_FLOOR",
    "REASON_BLOCKLISTED_COMPANY",
    "REASON_LOCATION_POLICY_VIOLATION",
    "REASON_POSTING_TOO_OLD",
    "REASON_TITLE_EXCLUDED",
    "REASON_TITLE_NOT_INCLUDED",
    "passes_hard_filters",
    "ScoredJob",
    "composite_score",
    "rank_jobs",
]
