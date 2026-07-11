"""Jobs feed navigation + scroll-and-settle pagination (spec T3.1).

No live jobright.ai access exists in this environment - this module is
built/testable against local `data:` URL or fixture HTML pages that
simulate a feed container with job cards, the same pattern Epic 1/2 used
(tests/test_selector_broken.py, tests/test_auth_login.py).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from src.observability.logging import log_event
from src.observability.selectors import SelectorRegistry, resolve_locator

DEFAULT_SCROLL_SETTLE_MS = 500
DEFAULT_NAV_TIMEOUT_MS = 8000

PaginationTerminationReason = Literal["no_new_cards", "max_pages_reached"]


@dataclass(frozen=True)
class PaginationResult:
    """Result of the scroll-and-settle loop (S3.1.2)."""

    cards: list  # list[Locator], one per visible job card
    pages_scrolled: int
    terminated_reason: PaginationTerminationReason


def navigate_to_jobs_feed(
    page,
    registry: SelectorRegistry,
    run_id: str,
    *,
    timeout_ms: int = DEFAULT_NAV_TIMEOUT_MS,
    logger=None,
) -> None:
    """S3.1.1: navigate to the JobRight Recommended/Jobs feed and confirm
    the feed container is structurally present.

    `jobs.feed_url` is looked up through the same centralized selector
    registry as every other semantic key (spec §3.3 says "ALL
    CSS/XPath selectors" live there, but the registry is really just a
    flat semantic-key -> string lookup - reusing it for the one entry-
    point URL this module needs avoids hardcoding it here, and keeps one
    file (selectors/jobright.yaml) as the single place to update when the
    real feed path is known).

    `jobs.feed_container` IS resolved via the raising `resolve_locator()`
    deliberately: a jobs feed that never renders its container at all is
    a genuine structural break (SelectorBroken -> HITL `selector_broken`),
    not a normal "zero results today" state - that distinction is handled
    separately in `scroll_and_collect_cards()` (zero/no-new cards is a
    completely normal outcome there).
    """
    feed_url = registry.get("jobs.feed_url")
    page.goto(feed_url)
    try:
        page.wait_for_load_state("networkidle", timeout=timeout_ms)
    except Exception:
        pass  # best-effort; the feed_container check below is authoritative

    resolve_locator(page, registry, "jobs.feed_container", run_id=run_id, timeout_ms=timeout_ms)

    if logger is not None:
        log_event(logger, "jobs_feed_navigated", run_id=run_id, feed_url=feed_url)


def scroll_and_collect_cards(
    page,
    registry: SelectorRegistry,
    run_id: str,
    *,
    max_pages: int = 10,
    settle_ms: int = DEFAULT_SCROLL_SETTLE_MS,
    logger=None,
) -> PaginationResult:
    """S3.1.2: infinite-scroll/pagination handler.

    Scrolls to the bottom of the page, waits for it to settle (a fixed
    pause plus a best-effort network-idle wait), and re-counts visible
    job cards, repeating until either a scroll produces no new cards
    (`no_new_cards`) or `max_pages` scroll iterations have happened
    (`max_pages_reached`) - both are normal, expected termination
    conditions, not errors.
    """
    card_selector = registry.get("jobs.card")
    previous_count = page.locator(card_selector).count()
    pages_scrolled = 0
    terminated_reason: PaginationTerminationReason = "max_pages_reached"

    while pages_scrolled < max_pages:
        try:
            page.mouse.wheel(0, 20_000)
        except Exception:
            pass
        try:
            page.wait_for_timeout(settle_ms)
        except Exception:
            pass
        try:
            page.wait_for_load_state("networkidle", timeout=settle_ms * 2)
        except Exception:
            pass

        pages_scrolled += 1
        current_count = page.locator(card_selector).count()
        if current_count <= previous_count:
            terminated_reason = "no_new_cards"
            break
        previous_count = current_count

    cards = page.locator(card_selector).all()

    if logger is not None:
        log_event(
            logger,
            "jobs_feed_pagination_done",
            run_id=run_id,
            pages_scrolled=pages_scrolled,
            card_count=len(cards),
            terminated_reason=terminated_reason,
        )

    return PaginationResult(
        cards=cards, pages_scrolled=pages_scrolled, terminated_reason=terminated_reason
    )
