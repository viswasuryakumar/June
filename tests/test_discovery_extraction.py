"""Tests for src/discovery/extraction.py (spec T3.2): card extraction,
job_id derivation, per-run extraction stats, and detail-page enrichment.

No live jobright.ai access exists in this environment - cards/detail
pages are simulated via local `data:` URL fixture HTML, matching the
pattern tests/test_selector_broken.py / tests/test_auth_login.py use
(p.chromium.launch(headless=True, channel="chromium") - only full
Chromium, not chrome-headless-shell, is installed in this sandbox).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

import pytest
from playwright.sync_api import sync_playwright
from src.contracts.models import Job
from src.discovery.extraction import (
    ExtractionStats,
    derive_job_id,
    enrich_job_detail,
    extract_job_card,
    log_extraction_stats,
)
from src.observability.selectors import SelectorRegistry

pytestmark = pytest.mark.playwright

FULL_REGISTRY = SelectorRegistry(
    {
        "jobs": {
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


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True, channel="chromium")
        yield b
        b.close()


class _ListHandler(logging.Handler):
    def __init__(self):
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


@pytest.fixture
def event_logger():
    """A plain, non-propagating logger with a list-collecting handler, so
    tests can assert on log_event()'s structured `extra` fields directly
    without going through configure_logging()'s JSON/redaction pipeline.
    """
    logger = logging.getLogger("test.discovery.extraction")
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    handler = _ListHandler()
    logger.addHandler(handler)
    yield logger, handler
    logger.removeHandler(handler)


def make_job(
    job_id: str = "job-x",
    jobright_url: str = "data:text/html,<html><body></body></html>",
    raw_description: str = "",
    external_url: str | None = None,
    apply_mode: str = "unknown",
) -> Job:
    return Job(
        job_id=job_id,
        title="Some Title",
        company="Some Co",
        location="Remote",
        jobright_url=jobright_url,
        raw_description=raw_description,
        external_url=external_url,
        apply_mode=apply_mode,
    )


# -- derive_job_id (pure function) -------------------------------------------


def test_derive_job_id_from_href_last_path_segment():
    job_id, from_href = derive_job_id("https://jobright.ai/jobs/abc123?ref=src", "fallback-seed")
    assert job_id == "abc123"
    assert from_href is True


def test_derive_job_id_strips_trailing_slash():
    job_id, from_href = derive_job_id("https://jobright.ai/jobs/abc123/", "fallback-seed")
    assert job_id == "abc123"
    assert from_href is True


def test_derive_job_id_synthetic_fallback_is_deterministic():
    job_id_1, from_href_1 = derive_job_id(None, "acme corp|backend engineer|remote|0")
    job_id_2, from_href_2 = derive_job_id(None, "acme corp|backend engineer|remote|0")

    assert from_href_1 is False
    assert from_href_2 is False
    assert job_id_1 == job_id_2
    assert job_id_1.startswith("unknown-")


def test_derive_job_id_synthetic_fallback_differs_for_different_seeds():
    job_id_a, _ = derive_job_id(None, "seed-a")
    job_id_b, _ = derive_job_id(None, "seed-b")
    assert job_id_a != job_id_b


# -- extract_job_card ---------------------------------------------------------

WELL_FORMED_CARD_HTML = (
    "<html><body>"
    "<div class='card'>"
    "<a class='card-link' href='https://jobright.ai/jobs/abc123?ref=src'>View</a>"
    "<div class='title'>Senior Backend Engineer</div>"
    "<div class='company'>Acme Corp</div>"
    "<div class='location'>Remote - USA</div>"
    "<div class='salary'>$120k - $150k</div>"
    "<div class='match-score'>87% match</div>"
    "<div class='posted'>3 days ago</div>"
    "</div>"
    "</body></html>"
)


def test_extract_job_card_well_formed(tmp_path, monkeypatch, browser):
    monkeypatch.chdir(tmp_path)
    page = browser.new_page()
    page.goto("data:text/html," + WELL_FORMED_CARD_HTML)
    card = page.locator(".card").first
    stats = ExtractionStats()

    job = extract_job_card(
        card, FULL_REGISTRY, base_url="https://jobright.ai", index=0, stats=stats
    )

    assert job.job_id == "abc123"
    assert job.title == "Senior Backend Engineer"
    assert job.company == "Acme Corp"
    assert job.location == "Remote - USA"
    assert job.remote_type == "remote"
    assert job.salary_min == 120000
    assert job.salary_max == 150000
    assert job.match_score == 87
    assert job.jobright_url == "https://jobright.ai/jobs/abc123?ref=src"
    assert job.apply_mode == "unknown"  # extract_job_card never sets apply_mode
    assert job.raw_description == ""

    now = datetime.now(UTC)
    assert job.posted_at is not None
    assert timedelta(days=2, hours=23) < (now - job.posted_at) < timedelta(days=3, hours=1)

    assert stats.cards_seen == 1
    assert stats.job_id_from_href == 1
    assert stats.job_id_synthetic == 0
    assert stats.missing_title == 0
    assert stats.missing_company == 0
    assert stats.missing_location == 0
    assert stats.missing_salary_min == 0
    assert stats.missing_salary_max == 0
    assert stats.missing_match_score == 0
    assert stats.missing_posted_at == 0
    page.close()


MISSING_FIELDS_CARD_HTML = (
    "<html><body>"
    "<div class='card'>"
    "<div class='title'>Data Analyst</div>"
    "<div class='company'>Beta LLC</div>"
    "<div class='location'>Austin, TX</div>"
    "</div>"
    "</body></html>"
)


def test_extract_job_card_missing_fields_degrade_to_model_defaults(tmp_path, monkeypatch, browser):
    monkeypatch.chdir(tmp_path)
    page = browser.new_page()
    page.goto("data:text/html," + MISSING_FIELDS_CARD_HTML)
    card = page.locator(".card").first
    stats = ExtractionStats()

    # Must not raise despite missing href/salary/match-score/posted-time.
    job = extract_job_card(
        card, FULL_REGISTRY, base_url="https://jobright.ai", index=0, stats=stats
    )

    assert job.title == "Data Analyst"
    assert job.company == "Beta LLC"
    assert job.location == "Austin, TX"
    assert job.remote_type == "onsite"
    assert job.salary_min is None
    assert job.salary_max is None
    assert job.match_score is None
    assert job.posted_at is None
    # job_id/jobright_url are required with no default - never omitted.
    assert job.job_id.startswith("unknown-")
    assert job.jobright_url == f"https://jobright.ai/jobs/{job.job_id}"

    assert stats.job_id_synthetic == 1
    assert stats.job_id_from_href == 0
    assert stats.missing_salary_min == 1
    assert stats.missing_salary_max == 1
    assert stats.missing_match_score == 1
    assert stats.missing_posted_at == 1
    assert stats.missing_title == 0
    assert stats.missing_company == 0
    assert stats.missing_location == 0
    page.close()


def test_extract_job_card_no_title_or_company_still_produces_job(tmp_path, monkeypatch, browser):
    monkeypatch.chdir(tmp_path)
    page = browser.new_page()
    page.goto("data:text/html,<html><body><div class='card'></div></body></html>")
    card = page.locator(".card").first
    stats = ExtractionStats()

    job = extract_job_card(
        card, FULL_REGISTRY, base_url="https://jobright.ai", index=2, stats=stats
    )

    assert job.title == ""
    assert job.company == ""
    assert job.location == ""
    assert job.remote_type == "unknown"
    assert job.job_id.startswith("unknown-")
    assert stats.missing_title == 1
    assert stats.missing_company == 1
    assert stats.missing_location == 1
    page.close()


TWO_IDENTICAL_CARDS_HTML = (
    "<html><body>"
    "<div class='card'>"
    "<div class='title'>QA Engineer</div>"
    "<div class='company'>Gamma Inc</div>"
    "<div class='location'>Denver, CO</div>"
    "</div>"
    "<div class='card'>"
    "<div class='title'>QA Engineer</div>"
    "<div class='company'>Gamma Inc</div>"
    "<div class='location'>Denver, CO</div>"
    "</div>"
    "</body></html>"
)


def test_extract_job_card_synthetic_id_is_deterministic_for_identical_cards(
    tmp_path, monkeypatch, browser
):
    monkeypatch.chdir(tmp_path)
    page = browser.new_page()
    page.goto("data:text/html," + TWO_IDENTICAL_CARDS_HTML)
    cards = page.locator(".card")
    stats = ExtractionStats()

    # Same explicit `index` both times (index is caller-supplied, not
    # derived from DOM position), so the fallback_seed is identical.
    job_1 = extract_job_card(
        cards.nth(0), FULL_REGISTRY, base_url="https://jobright.ai", index=0, stats=stats
    )
    job_2 = extract_job_card(
        cards.nth(1), FULL_REGISTRY, base_url="https://jobright.ai", index=0, stats=stats
    )

    assert job_1.job_id == job_2.job_id
    assert job_1.job_id.startswith("unknown-")
    page.close()


SINGLE_SALARY_CARD_HTML = (
    "<html><body>"
    "<div class='card'>"
    "<a class='card-link' href='https://jobright.ai/jobs/single-sal'>View</a>"
    "<div class='title'>Support Engineer</div>"
    "<div class='company'>Delta Co</div>"
    "<div class='location'>Chicago, IL</div>"
    "<div class='salary'>$90k</div>"
    "</div>"
    "</body></html>"
)


def test_extract_job_card_single_salary_value_sets_min_equal_max(tmp_path, monkeypatch, browser):
    monkeypatch.chdir(tmp_path)
    page = browser.new_page()
    page.goto("data:text/html," + SINGLE_SALARY_CARD_HTML)
    card = page.locator(".card").first
    stats = ExtractionStats()

    job = extract_job_card(
        card, FULL_REGISTRY, base_url="https://jobright.ai", index=0, stats=stats
    )

    assert job.salary_min == 90000
    assert job.salary_max == 90000
    assert stats.missing_salary_min == 0
    assert stats.missing_salary_max == 0
    page.close()


UNPARSABLE_POSTED_AT_CARD_HTML = (
    "<html><body>"
    "<div class='card'>"
    "<a class='card-link' href='https://jobright.ai/jobs/abs-date'>View</a>"
    "<div class='title'>Ops Engineer</div>"
    "<div class='company'>Epsilon LLC</div>"
    "<div class='location'>Onsite - Boston</div>"
    "<div class='posted'>Jul 5</div>"
    "</div>"
    "</body></html>"
)


def test_extract_job_card_unparsable_posted_at_degrades_to_none_not_raise(
    tmp_path, monkeypatch, browser
):
    """Documented design choice (extraction.py:_extract_posted_at): an
    absolute-date or otherwise-unrecognized posted-time phrasing degrades
    to None rather than raising or guessing - the freshness filter treats
    None as "not positively known to be stale" (see test_discovery_dedupe.py).
    """
    monkeypatch.chdir(tmp_path)
    page = browser.new_page()
    page.goto("data:text/html," + UNPARSABLE_POSTED_AT_CARD_HTML)
    card = page.locator(".card").first
    stats = ExtractionStats()

    job = extract_job_card(
        card, FULL_REGISTRY, base_url="https://jobright.ai", index=0, stats=stats
    )

    assert job.posted_at is None
    assert stats.missing_posted_at == 1
    page.close()


# -- enrich_job_detail: the four apply_mode outcomes --------------------------

DETAIL_AGENT_HTML = (
    "<html><body>"
    "<div id='description'>Full JD text for the agent-apply role.</div>"
    "<button id='agent-btn'>Apply with Agent</button>"
    "</body></html>"
)
DETAIL_EXTENSION_HTML = (
    "<html><body><button id='extension-btn'>Autofill with Extension</button></body></html>"
)
DETAIL_MANUAL_ONLY_HTML = (
    "<html><body>"
    "<a id='external-link' href='https://careers.example.com/apply/123'>Apply on company site</a>"
    "</body></html>"
)
DETAIL_UNKNOWN_HTML = "<html><body><p>No apply affordance at all.</p></body></html>"


def test_enrich_job_detail_agent_mode(tmp_path, monkeypatch, browser):
    monkeypatch.chdir(tmp_path)
    detail_page = browser.new_page()
    job = make_job(job_id="job-agent", jobright_url="data:text/html," + DETAIL_AGENT_HTML)

    updated = enrich_job_detail(detail_page, FULL_REGISTRY, job, run_id="run-detail-agent")

    assert updated.apply_mode == "agent"
    assert "Full JD text" in updated.raw_description
    detail_page.close()


def test_enrich_job_detail_extension_mode(tmp_path, monkeypatch, browser):
    monkeypatch.chdir(tmp_path)
    detail_page = browser.new_page()
    job = make_job(
        job_id="job-ext",
        jobright_url="data:text/html," + DETAIL_EXTENSION_HTML,
        raw_description="old desc",
    )

    updated = enrich_job_detail(detail_page, FULL_REGISTRY, job, run_id="run-detail-ext")

    assert updated.apply_mode == "extension"
    # No #description on this page - default (existing) description preserved.
    assert updated.raw_description == "old desc"
    detail_page.close()


def test_enrich_job_detail_manual_only_mode(tmp_path, monkeypatch, browser):
    monkeypatch.chdir(tmp_path)
    detail_page = browser.new_page()
    job = make_job(job_id="job-manual", jobright_url="data:text/html," + DETAIL_MANUAL_ONLY_HTML)

    updated = enrich_job_detail(detail_page, FULL_REGISTRY, job, run_id="run-detail-manual")

    assert updated.apply_mode == "manual_only"
    assert updated.external_url == "https://careers.example.com/apply/123"
    detail_page.close()


def test_enrich_job_detail_unknown_mode(tmp_path, monkeypatch, browser):
    monkeypatch.chdir(tmp_path)
    detail_page = browser.new_page()
    job = make_job(job_id="job-unknown", jobright_url="data:text/html," + DETAIL_UNKNOWN_HTML)

    updated = enrich_job_detail(detail_page, FULL_REGISTRY, job, run_id="run-detail-unknown")

    assert updated.apply_mode == "unknown"
    assert updated.external_url is None
    detail_page.close()


def test_enrich_job_detail_navigation_failure_returns_job_unchanged(
    tmp_path, monkeypatch, browser, event_logger
):
    """Confirms the Code agent's own smoke-tested fix: a goto() failure
    (genuinely unreachable URL) must be caught and must return the job
    unchanged rather than crashing the whole discovery run.
    """
    monkeypatch.chdir(tmp_path)
    logger, handler = event_logger
    detail_page = browser.new_page()
    job = make_job(
        job_id="job-dead",
        jobright_url="https://this-domain-does-not-exist-at-all.invalid/nope",
        apply_mode="unknown",
    )

    updated = enrich_job_detail(
        detail_page, FULL_REGISTRY, job, run_id="run-dead", timeout_ms=3000, logger=logger
    )

    assert updated is job
    assert updated.apply_mode == "unknown"

    failure_records = [
        r for r in handler.records if getattr(r, "step", None) == "job_detail_navigation_failed"
    ]
    assert len(failure_records) == 1
    assert failure_records[0].job_id == "job-dead"
    detail_page.close()


# -- ExtractionStats / log_extraction_stats -----------------------------------

BATCH_CARDS_HTML = (
    "<html><body>"
    # Card A: fully populated.
    "<div class='card'>"
    "<a class='card-link' href='https://jobright.ai/jobs/card-a'>View</a>"
    "<div class='title'>Role A</div><div class='company'>Company A</div>"
    "<div class='location'>Remote</div><div class='salary'>$100k - $120k</div>"
    "<div class='match-score'>75% match</div><div class='posted'>1 day ago</div>"
    "</div>"
    # Card B: missing title.
    "<div class='card'>"
    "<a class='card-link' href='https://jobright.ai/jobs/card-b'>View</a>"
    "<div class='company'>Company B</div>"
    "<div class='location'>Remote</div><div class='salary'>$100k - $120k</div>"
    "<div class='match-score'>75% match</div><div class='posted'>1 day ago</div>"
    "</div>"
    # Card C: missing salary.
    "<div class='card'>"
    "<a class='card-link' href='https://jobright.ai/jobs/card-c'>View</a>"
    "<div class='title'>Role C</div><div class='company'>Company C</div>"
    "<div class='location'>Remote</div>"
    "<div class='match-score'>75% match</div><div class='posted'>1 day ago</div>"
    "</div>"
    # Card D: missing match_score.
    "<div class='card'>"
    "<a class='card-link' href='https://jobright.ai/jobs/card-d'>View</a>"
    "<div class='title'>Role D</div><div class='company'>Company D</div>"
    "<div class='location'>Remote</div><div class='salary'>$100k - $120k</div>"
    "<div class='posted'>1 day ago</div>"
    "</div>"
    # Card E: missing posted_at.
    "<div class='card'>"
    "<a class='card-link' href='https://jobright.ai/jobs/card-e'>View</a>"
    "<div class='title'>Role E</div><div class='company'>Company E</div>"
    "<div class='location'>Remote</div><div class='salary'>$100k - $120k</div>"
    "<div class='match-score'>75% match</div>"
    "</div>"
    "</body></html>"
)


def test_extraction_stats_counts_missing_fields_across_batch(
    tmp_path, monkeypatch, browser, event_logger
):
    monkeypatch.chdir(tmp_path)
    logger, handler = event_logger
    page = browser.new_page()
    page.goto("data:text/html," + BATCH_CARDS_HTML)
    cards = page.locator(".card").all()
    assert len(cards) == 5
    stats = ExtractionStats()

    for i, card in enumerate(cards):
        extract_job_card(card, FULL_REGISTRY, base_url="https://jobright.ai", index=i, stats=stats)

    assert stats.cards_seen == 5
    assert stats.job_id_from_href == 5
    assert stats.job_id_synthetic == 0
    assert stats.missing_title == 1  # card B
    assert stats.missing_company == 0
    assert stats.missing_location == 0
    assert stats.missing_salary_min == 1  # card C
    assert stats.missing_salary_max == 1  # card C
    assert stats.missing_match_score == 1  # card D
    assert stats.missing_posted_at == 1  # card E

    log_extraction_stats(logger, "run-stats-1", stats)
    stat_records = [
        r for r in handler.records if getattr(r, "step", None) == "job_extraction_stats"
    ]
    assert len(stat_records) == 1
    record = stat_records[0]
    assert record.cards_seen == 5
    assert record.missing_title == 1
    assert record.missing_salary_min == 1
    assert record.missing_match_score == 1
    assert record.missing_posted_at == 1
    page.close()


def test_log_extraction_stats_noop_when_logger_none():
    # Must not raise when no logger is configured (best-effort logging).
    log_extraction_stats(None, "run-noop", ExtractionStats())
