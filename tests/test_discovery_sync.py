"""Tests for src/discovery/sync.py (spec EPIC 3 orchestration): full
`sync_jobs()` idempotency and the terminal/in-flight status skip.

No live jobright.ai access exists in this environment - the feed is a
local `data:` URL fixture page with 3 static job cards, matching the
pattern used throughout Epic 2/3's other test modules.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import sync_playwright
from src.config.settings import Settings
from src.discovery.sync import sync_jobs
from src.observability.selectors import SelectorRegistry
from src.tracker.repository import InMemoryTrackerRepository

pytestmark = pytest.mark.playwright

FEED_HTML = (
    "<html><body>"
    "<div id='feed'>"
    "<div class='card'>"
    "<a class='card-link' href='https://jobright.ai/jobs/job-a'>View</a>"
    "<div class='title'>Job A Title</div><div class='company'>Company A</div>"
    "<div class='location'>Remote</div><div class='salary'>$100k - $120k</div>"
    "<div class='match-score'>40% match</div><div class='posted'>1 day ago</div>"
    "</div>"
    "<div class='card'>"
    "<a class='card-link' href='https://jobright.ai/jobs/job-b'>View</a>"
    "<div class='title'>Job B Title</div><div class='company'>Company B</div>"
    "<div class='location'>Onsite - NY</div><div class='salary'>$90k</div>"
    "<div class='match-score'>85% match</div><div class='posted'>2 days ago</div>"
    "</div>"
    "<div class='card'>"
    "<a class='card-link' href='https://jobright.ai/jobs/job-c'>View</a>"
    "<div class='title'>Job C Title</div><div class='company'>Company C</div>"
    "<div class='location'>Hybrid - SF</div><div class='salary'>$110k - $130k</div>"
    "<div class='match-score'>92% match</div><div class='posted'>3 days ago</div>"
    "</div>"
    "</div>"
    "</body></html>"
)


def build_registry(feed_html: str) -> SelectorRegistry:
    return SelectorRegistry(
        {
            "jobs": {
                "feed_url": "data:text/html," + feed_html,
                "feed_container": "#feed",
                "card": ".card",
                "card_link": "a.card-link",
                "title": ".title",
                "company": ".company",
                "location": ".location",
                "salary": ".salary",
                "match_score": ".match-score",
                "posted_at": ".posted",
                "detail_description": "#description",
                "detail_external_link": "a#external-link",
            },
            "apply": {
                "agent_button": "#agent-btn",
                "extension_autofill_button": "#extension-btn",
            },
        }
    )


def make_settings(**overrides) -> Settings:
    defaults = dict(
        max_applications_per_day=10,
        min_match_score=70,
        max_discovery_pages=10,
        discovery_enrichment_score_threshold=50,
        max_posting_age_days=30,
    )
    defaults.update(overrides)
    return Settings(**defaults)


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True, channel="chromium")
        yield b
        b.close()


def test_sync_jobs_twice_back_to_back_is_idempotent(tmp_path, monkeypatch, browser):
    monkeypatch.chdir(tmp_path)
    registry = build_registry(FEED_HTML)
    tracker = InMemoryTrackerRepository()
    # Never enrich here (100 is the highest possible match_score) -
    # idempotency of ingestion/dedupe is what's under test, not
    # detail-page enrichment (covered in test_discovery_extraction.py).
    settings = make_settings(discovery_enrichment_score_threshold=100)
    context = browser.new_context()
    page = context.new_page()

    result1 = sync_jobs(page, registry, tracker, settings, "run-idem-1")
    result2 = sync_jobs(page, registry, tracker, settings, "run-idem-2")

    assert result1.jobs_seen == 3
    assert result1.jobs_ingested == 3
    assert result1.jobs_refreshed == 0
    assert result1.extraction_stats["cards_seen"] == 3
    assert result1.jobs_enriched == 0

    # REV-004: the second run re-extracts the same still-`discovered`
    # cards (should_skip_existing() only skips past-`discovered` status),
    # but nothing is genuinely new to the tracker - that must show up as
    # jobs_refreshed, not get miscounted as jobs_ingested again.
    assert result2.jobs_seen == 3
    assert result2.jobs_ingested == 0
    assert result2.jobs_refreshed == 3

    # No duplicate rows on the second run: still exactly 3 tracker records.
    records = tracker.get_jobs()
    assert len(records) == 3
    assert sorted(r.job_id for r in records) == ["job-a", "job-b", "job-c"]
    context.close()


def test_sync_jobs_skips_terminal_status_and_does_not_reenrich_it(tmp_path, monkeypatch, browser):
    monkeypatch.chdir(tmp_path)
    registry = build_registry(FEED_HTML)
    tracker = InMemoryTrackerRepository()
    # job-b (85) and job-c (92) clear the threshold; job-a (40) doesn't.
    settings = make_settings(discovery_enrichment_score_threshold=50)
    context = browser.new_context()
    page = context.new_page()

    enriched_calls: list[str] = []

    def spy_enrich(detail_page, registry_, job, *, run_id, logger=None):
        enriched_calls.append(job.job_id)
        return job

    monkeypatch.setattr("src.discovery.sync.enrich_job_detail", spy_enrich)

    result1 = sync_jobs(page, registry, tracker, settings, "run-status-1")
    assert result1.jobs_ingested == 3
    assert result1.jobs_refreshed == 0
    assert result1.jobs_enriched == 2
    assert sorted(enriched_calls) == ["job-b", "job-c"]

    # Simulate a prior run having fully progressed job-b to a terminal
    # state (selected -> skipped is a legal transition per the state
    # machine's allow-list).
    tracker.transition("job-b", "selected")
    tracker.transition("job-b", "skipped")

    enriched_calls.clear()
    result2 = sync_jobs(page, registry, tracker, settings, "run-status-2")

    assert "job-b" not in enriched_calls  # terminal status -> skipped before enrichment
    assert "job-c" in enriched_calls  # still "discovered" -> processed/enriched again
    assert result2.jobs_skipped_existing == 1
    # job-a and job-c were already tracked in status `discovered` from
    # run-status-1 - neither is genuinely new, so both refresh rather than
    # re-ingest (REV-004).
    assert result2.jobs_ingested == 0
    assert result2.jobs_refreshed == 2
    assert tracker.get_job("job-b").status == "skipped"  # untouched, not reset
    context.close()


def test_sync_jobs_skips_inflight_status_too(tmp_path, monkeypatch, browser):
    monkeypatch.chdir(tmp_path)
    registry = build_registry(FEED_HTML)
    tracker = InMemoryTrackerRepository()
    settings = make_settings(discovery_enrichment_score_threshold=100)
    context = browser.new_context()
    page = context.new_page()

    result1 = sync_jobs(page, registry, tracker, settings, "run-inflight-1")
    assert result1.jobs_ingested == 3
    assert result1.jobs_refreshed == 0
    tracker.transition("job-a", "selected")  # in-flight, not terminal

    result2 = sync_jobs(page, registry, tracker, settings, "run-inflight-2")

    assert result2.jobs_skipped_existing == 1
    assert tracker.get_job("job-a").status == "selected"
    # The other two jobs are still "discovered" (already tracked from
    # run-inflight-1) - re-extracted but not genuinely new, so they
    # refresh rather than re-ingest (REV-004).
    assert result2.jobs_ingested == 0
    assert result2.jobs_refreshed == 2
    context.close()
