"""Tests for src/discovery/feed.py (spec T3.1): jobs-feed navigation and
the scroll-and-settle pagination loop's two termination paths.

No live jobright.ai access exists in this environment - the feed is
simulated via local `data:` URL fixture pages. The pagination fixtures
below use an inline <script> that reacts to Playwright's real 'wheel' DOM
event (page.mouse.wheel() dispatches a genuine wheel input event that
Chromium delivers to the page), so both termination paths
(`no_new_cards` / `max_pages_reached`) are actually exercised by growing
the DOM live, rather than asserted against a static page.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import sync_playwright
from src.contracts.exceptions import SelectorBroken
from src.discovery.feed import navigate_to_jobs_feed, scroll_and_collect_cards
from src.observability.selectors import SelectorRegistry

pytestmark = pytest.mark.playwright


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True, channel="chromium")
        yield b
        b.close()


def _registry(feed_url: str) -> SelectorRegistry:
    return SelectorRegistry(
        {
            "jobs": {
                "feed_url": feed_url,
                "feed_container": "#feed",
                "card": ".card",
            }
        }
    )


# Starts with 1 card; appends one more on each of the first 2 'wheel'
# events, then stops - so the 3rd scroll iteration sees no growth and the
# loop terminates via `no_new_cards`.
GROWS_THEN_STOPS_HTML = (
    "<html><body>"
    "<div id='feed'><div class='card'>Card 0</div></div>"
    "<script>"
    "var n = 1;"
    "window.addEventListener('wheel', function() {"
    "  if (n < 3) {"
    "    var d = document.createElement('div');"
    "    d.className = 'card';"
    "    d.textContent = 'Card ' + n;"
    "    document.getElementById('feed').appendChild(d);"
    "    n++;"
    "  }"
    "});"
    "</script>"
    "</body></html>"
)

# Appends a brand-new card on *every* 'wheel' event, forever - never
# self-terminates via no_new_cards, so only the `max_pages` cap can stop
# the loop.
GROWS_FOREVER_HTML = (
    "<html><body>"
    "<div id='feed'><div class='card'>Card 0</div></div>"
    "<script>"
    "var n = 1;"
    "window.addEventListener('wheel', function() {"
    "  var d = document.createElement('div');"
    "  d.className = 'card';"
    "  d.textContent = 'Card ' + n;"
    "  document.getElementById('feed').appendChild(d);"
    "  n++;"
    "});"
    "</script>"
    "</body></html>"
)


# -- navigate_to_jobs_feed ----------------------------------------------------


def test_navigate_to_jobs_feed_confirms_container(tmp_path, monkeypatch, browser):
    monkeypatch.chdir(tmp_path)
    page = browser.new_page()
    registry = _registry("data:text/html,<html><body><div id='feed'>ok</div></body></html>")

    navigate_to_jobs_feed(page, registry, "run-feed-1", timeout_ms=2000)

    assert page.locator("#feed").is_visible()
    page.close()


def test_navigate_to_jobs_feed_raises_selector_broken_when_container_missing(
    tmp_path, monkeypatch, browser
):
    monkeypatch.chdir(tmp_path)
    page = browser.new_page()
    registry = _registry("data:text/html,<html><body>no feed here</body></html>")

    with pytest.raises(SelectorBroken) as excinfo:
        navigate_to_jobs_feed(page, registry, "run-feed-2", timeout_ms=300)

    assert excinfo.value.selector_key == "jobs.feed_container"
    page.close()


# -- scroll_and_collect_cards --------------------------------------------------


def test_scroll_and_collect_cards_terminates_on_no_new_cards(tmp_path, monkeypatch, browser):
    monkeypatch.chdir(tmp_path)
    page = browser.new_page()
    page.goto("data:text/html," + GROWS_THEN_STOPS_HTML)
    registry = _registry("data:text/html,unused")

    result = scroll_and_collect_cards(page, registry, "run-scroll-1", max_pages=10, settle_ms=100)

    # Grew 1 -> 2 -> 3 over the first two scrolls, then a third scroll
    # confirmed no further growth: 3 scroll iterations total.
    assert result.terminated_reason == "no_new_cards"
    assert result.pages_scrolled == 3
    assert len(result.cards) == 3
    page.close()


def test_scroll_and_collect_cards_terminates_on_max_pages_reached_even_if_still_growing(
    tmp_path, monkeypatch, browser
):
    monkeypatch.chdir(tmp_path)
    page = browser.new_page()
    page.goto("data:text/html," + GROWS_FOREVER_HTML)
    registry = _registry("data:text/html,unused")

    result = scroll_and_collect_cards(page, registry, "run-scroll-2", max_pages=3, settle_ms=100)

    # Every single iteration grew the card count (never self-terminates
    # via no_new_cards) - only the max_pages cap stops it.
    assert result.terminated_reason == "max_pages_reached"
    assert result.pages_scrolled == 3
    assert len(result.cards) == 1 + 3  # initial card + one per iteration
    page.close()
