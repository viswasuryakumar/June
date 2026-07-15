"""Tests for src/observability/human.py — human-like browser behavior.

These tests verify the pure-Python logic (Bezier curves, point selection,
delay randomization) without requiring a real browser. The Playwright-
dependent functions (`crawl_page`, `human_settle`, `human_click`,
`human_type`) are tested with a mock page object that records calls.
"""

from __future__ import annotations

import random
import time
from unittest.mock import MagicMock

import pytest
from src.observability.human import (
    DEFAULT_SETTLE_MAX_S,
    DEFAULT_SETTLE_MIN_S,
    _bezier_point,
    _get_random_visible_points,
    _human_move,
    crawl_page,
    human_click,
    human_fill_locator,
    human_hover_locator,
    human_settle,
    human_type,
)

# ---------------------------------------------------------------------------
# _bezier_point
# ---------------------------------------------------------------------------


class TestBezierPoint:
    def test_endpoints_exact(self):
        """At t=0 the point is p0; at t=1 the point is p3."""
        p0, p1, p2, p3 = (0, 0), (50, 100), (150, 100), (200, 0)
        assert _bezier_point(0.0, p0, p1, p2, p3) == (0.0, 0.0)
        assert _bezier_point(1.0, p0, p1, p2, p3) == (200.0, 0.0)

    def test_midpoint_is_between_endpoints(self):
        """At t=0.5 the point should be between p0 and p3."""
        p0, p1, p2, p3 = (0, 0), (50, 100), (150, 100), (200, 0)
        x, y = _bezier_point(0.5, p0, p1, p2, p3)
        assert 0 < x < 200
        # the curve bulges upward (y > 0) at the midpoint
        assert y > 0

    def test_straight_line_when_control_points_on_line(self):
        """If all control points are colinear, the curve is a straight line."""
        p0, p1, p2, p3 = (0, 0), (33, 0), (66, 0), (100, 0)
        for t in (0.0, 0.25, 0.5, 0.75, 1.0):
            _, y = _bezier_point(t, p0, p1, p2, p3)
            assert y == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# _human_move
# ---------------------------------------------------------------------------


class TestHumanMove:
    def test_calls_mouse_move_multiple_times(self):
        """_human_move should call page.mouse.move at least `steps` times."""
        page = MagicMock()
        page.mouse.move = MagicMock()
        _human_move(page, (0, 0), (100, 100), steps=10, step_delay_ms=0)
        assert page.mouse.move.call_count == 10

    def test_first_move_near_start(self):
        """The first interpolated point should be close to the start point."""
        page = MagicMock()
        page.mouse.move = MagicMock()
        _human_move(page, (100, 200), (300, 400), steps=5, step_delay_ms=0)
        first_call = page.mouse.move.call_args_list[0]
        x, y = first_call.args
        # first step is t=1/5=0.2, so it should be closer to start than end
        assert abs(x - 100) < 100
        assert abs(y - 200) < 100

    def test_last_move_near_end(self):
        """The last interpolated point should be close to the end point."""
        page = MagicMock()
        page.mouse.move = MagicMock()
        _human_move(page, (100, 200), (300, 400), steps=5, step_delay_ms=0)
        last_call = page.mouse.move.call_args_list[-1]
        x, y = last_call.args
        assert abs(x - 300) < 30  # jitter is ±2px, plus curve offset
        assert abs(y - 400) < 30

    def test_swallows_mouse_move_exception(self):
        """If page.mouse.move raises, _human_move should not propagate."""
        page = MagicMock()
        page.mouse.move = MagicMock(side_effect=RuntimeError("page closed"))
        # should not raise
        _human_move(page, (0, 0), (100, 100), steps=5, step_delay_ms=0)


# ---------------------------------------------------------------------------
# _get_random_visible_points
# ---------------------------------------------------------------------------


class TestGetRandomVisiblePoints:
    def test_returns_requested_count_with_mock_elements(self):
        """When page.evaluate returns visible element boxes, we get points
        back near those boxes."""
        page = MagicMock()
        page.viewport_size = {"width": 1440, "height": 900}
        boxes = [
            {"x": 100, "y": 100},
            {"x": 500, "y": 300},
            {"x": 800, "y": 600},
        ]
        page.evaluate = MagicMock(return_value=boxes)
        points = _get_random_visible_points(page, 3)
        assert len(points) == 3
        for px, py in points:
            assert 0 <= px <= 1440
            assert 0 <= py <= 900

    def test_falls_back_to_random_viewport_points(self):
        """When page.evaluate raises, we still get `count` random points."""
        page = MagicMock()
        page.viewport_size = {"width": 1440, "height": 900}
        page.evaluate = MagicMock(side_effect=RuntimeError("no DOM"))
        points = _get_random_visible_points(page, 5)
        assert len(points) == 5
        for px, py in points:
            assert 50 <= px <= 1390
            assert 50 <= py <= 850

    def test_fills_remaining_with_random_when_fewer_boxes(self):
        """If we get fewer element boxes than requested, the rest are random."""
        page = MagicMock()
        page.viewport_size = {"width": 1440, "height": 900}
        boxes = [{"x": 100, "y": 100}]
        page.evaluate = MagicMock(return_value=boxes)
        points = _get_random_visible_points(page, 4)
        assert len(points) == 4


# ---------------------------------------------------------------------------
# crawl_page
# ---------------------------------------------------------------------------


class TestCrawlPage:
    def test_returns_positive_hover_count(self):
        """crawl_page should visit at least one point in a short duration."""
        page = MagicMock()
        page.viewport_size = {"width": 1440, "height": 900}
        page.mouse.move = MagicMock()
        page.mouse.wheel = MagicMock()
        page.wait_for_timeout = MagicMock()
        page.evaluate = MagicMock(return_value=[{"x": 100, "y": 100}, {"x": 500, "y": 300}])
        visited = crawl_page(page, duration_s=0.1, hover_min_ms=10, hover_max_ms=20)
        assert visited >= 1

    def test_respects_deadline(self):
        """crawl_page should not run significantly longer than duration_s."""
        page = MagicMock()
        page.viewport_size = {"width": 1440, "height": 900}
        page.mouse.move = MagicMock()
        page.mouse.wheel = MagicMock()
        page.wait_for_timeout = MagicMock()
        page.evaluate = MagicMock(return_value=[{"x": 100, "y": 100}])
        start = time.monotonic()
        crawl_page(page, duration_s=0.2, hover_min_ms=5, hover_max_ms=10)
        elapsed = time.monotonic() - start
        assert elapsed < 2.0

    def test_uses_custom_rng(self):
        """crawl_page should accept a custom random.Random for determinism."""
        page = MagicMock()
        page.viewport_size = {"width": 1440, "height": 900}
        page.mouse.move = MagicMock()
        page.mouse.wheel = MagicMock()
        page.wait_for_timeout = MagicMock()
        page.evaluate = MagicMock(return_value=[{"x": 100, "y": 100}])
        rng = random.Random(42)
        visited = crawl_page(page, duration_s=0.1, hover_min_ms=5, hover_max_ms=10, rng=rng)
        assert visited >= 1


# ---------------------------------------------------------------------------
# human_settle
# ---------------------------------------------------------------------------


class TestHumanSettle:
    def test_zero_delay_returns_immediately(self):
        """When min_s=max_s=0, human_settle should return instantly with
        zero delay and zero hover points."""
        page = MagicMock()
        result = human_settle(page, min_s=0, max_s=0)
        assert result["delay_s"] == 0.0
        assert result["hover_points"] == 0
        page.mouse.move.assert_not_called()

    def test_returns_delay_and_hover_stats(self):
        """human_settle should return a dict with delay_s and hover_points."""
        page = MagicMock()
        page.viewport_size = {"width": 1440, "height": 900}
        page.mouse.move = MagicMock()
        page.mouse.wheel = MagicMock()
        page.wait_for_timeout = MagicMock()
        page.evaluate = MagicMock(return_value=[{"x": 100, "y": 100}])
        result = human_settle(page, min_s=0.1, max_s=0.2)
        assert "delay_s" in result
        assert "hover_points" in result
        assert result["hover_points"] >= 1
        assert result["delay_s"] > 0

    def test_delay_is_within_range(self):
        """The actual delay should be within [min_s, max_s] (with slack)."""
        page = MagicMock()
        page.viewport_size = {"width": 1440, "height": 900}
        page.mouse.move = MagicMock()
        page.mouse.wheel = MagicMock()
        page.wait_for_timeout = MagicMock()
        page.evaluate = MagicMock(return_value=[{"x": 100, "y": 100}])
        rng = random.Random(0)
        result = human_settle(page, min_s=0.1, max_s=0.3, rng=rng)
        assert result["delay_s"] <= 2.0

    def test_default_range_is_zero_to_sixteen(self):
        """The default range should be 0 to 16 seconds (user-requested)."""
        assert DEFAULT_SETTLE_MIN_S == 0.0
        assert DEFAULT_SETTLE_MAX_S == 16.0


# ---------------------------------------------------------------------------
# human_click
# ---------------------------------------------------------------------------


class TestHumanClick:
    def test_moves_mouse_before_clicking(self):
        """human_click should call mouse.move before clicking the locator."""
        page = MagicMock()
        page.viewport_size = {"width": 1440, "height": 900}
        page.mouse.move = MagicMock()
        page.wait_for_timeout = MagicMock()
        locator = MagicMock()
        locator.wait_for = MagicMock()
        locator.bounding_box = MagicMock(
            return_value={"x": 100, "y": 100, "width": 80, "height": 30}
        )
        locator.click = MagicMock()
        page.locator = MagicMock(return_value=MagicMock(first=locator))

        human_click(page, "button#submit", pre_hover_s=0.01)

        assert page.mouse.move.call_count > 0
        locator.click.assert_called_once()

    def test_falls_back_to_direct_click_on_no_box(self):
        """If bounding_box returns None, human_click should click directly."""
        page = MagicMock()
        page.viewport_size = {"width": 1440, "height": 900}
        page.mouse.move = MagicMock()
        page.wait_for_timeout = MagicMock()
        locator = MagicMock()
        locator.wait_for = MagicMock()
        locator.bounding_box = MagicMock(return_value=None)
        locator.click = MagicMock()
        page.locator = MagicMock(return_value=MagicMock(first=locator))

        human_click(page, "button#submit")

        locator.click.assert_called_once()
        page.mouse.move.assert_not_called()

    def test_falls_back_on_wait_exception(self):
        """If wait_for raises, human_click should fall back to direct click."""
        page = MagicMock()
        page.viewport_size = {"width": 1440, "height": 900}
        page.mouse.move = MagicMock()
        page.wait_for_timeout = MagicMock()
        locator = MagicMock()
        locator.wait_for = MagicMock(side_effect=TimeoutError("not found"))
        locator.click = MagicMock()
        page.locator = MagicMock(return_value=MagicMock(first=locator))

        human_click(page, "button#missing")

        locator.click.assert_called_once()


# ---------------------------------------------------------------------------
# human_type
# ---------------------------------------------------------------------------


class TestHumanType:
    def test_types_each_character(self):
        """human_type should call keyboard.type once per character."""
        page = MagicMock()
        page.wait_for_timeout = MagicMock()
        page.keyboard.type = MagicMock()
        locator = MagicMock()
        page.locator = MagicMock(return_value=MagicMock(first=locator))

        human_type(page, "input#name", "hello", per_char_min_ms=0, per_char_max_ms=1)

        assert page.keyboard.type.call_count == 5
        typed_chars = [call.args[0] for call in page.keyboard.type.call_args_list]
        assert "".join(typed_chars) == "hello"

    def test_clicks_locator_before_typing(self):
        """human_type should click the locator before typing."""
        page = MagicMock()
        page.wait_for_timeout = MagicMock()
        page.keyboard.type = MagicMock()
        locator = MagicMock()
        locator.click = MagicMock()
        page.locator = MagicMock(return_value=MagicMock(first=locator))

        human_type(page, "input#name", "hi", per_char_min_ms=0, per_char_max_ms=1)

        locator.click.assert_called_once()

    def test_longer_pause_on_spaces_and_punctuation(self):
        """Spaces and punctuation should have longer delays."""
        page = MagicMock()
        page.keyboard.type = MagicMock()
        locator = MagicMock()
        page.locator = MagicMock(return_value=MagicMock(first=locator))

        human_type(page, "input#name", "a b.c", per_char_min_ms=1, per_char_max_ms=2)

        assert page.keyboard.type.call_count == 5
        timeout_calls = page.wait_for_timeout.call_args_list
        # char 0: 'a' → delay 1-2ms
        # char 1: ' ' → delay 80-250ms
        # char 2: 'b' → delay 1-2ms
        # char 3: '.' → delay 80-250ms
        # char 4: 'c' → delay 1-2ms
        space_delay = timeout_calls[1].args[0]
        dot_delay = timeout_calls[3].args[0]
        assert space_delay >= 80
        assert dot_delay >= 80


# ---------------------------------------------------------------------------
# human_hover_locator / human_fill_locator (locator-based, for form fields)
# ---------------------------------------------------------------------------


class TestHumanHoverLocator:
    def test_scrolls_and_hovers_before_acting(self):
        locator = MagicMock()
        locator.page = MagicMock()
        human_hover_locator(locator, pre_hover_s=0.0)
        locator.scroll_into_view_if_needed.assert_called_once()
        locator.hover.assert_called_once()

    def test_never_raises_when_control_detached(self):
        locator = MagicMock()
        locator.page = MagicMock()
        locator.scroll_into_view_if_needed.side_effect = Exception("detached")
        locator.hover.side_effect = Exception("detached")
        human_hover_locator(locator)  # must not raise


class TestHumanFillLocator:
    def test_types_each_character_not_instant_fill(self):
        locator = MagicMock()
        page = MagicMock()
        locator.page = page
        human_fill_locator(locator, "abc", rng=random.Random(1))
        typed = "".join(c.args[0] for c in page.keyboard.type.call_args_list)
        assert typed == "abc"
        # cleared before typing, and never used a single instant fill of the value
        locator.fill.assert_called_once_with("")

    def test_falls_back_to_fill_when_typing_breaks(self):
        locator = MagicMock()
        page = MagicMock()
        page.keyboard.type.side_effect = Exception("target closed")
        locator.page = page
        human_fill_locator(locator, "hello", rng=random.Random(1))
        # after typing fails it sets the value directly (best-effort)
        locator.fill.assert_any_call("hello")
