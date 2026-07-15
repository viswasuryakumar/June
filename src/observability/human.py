"""Human-like browser behavior to reduce bot-detection risk (spec §6).

Websites detect automation through several signals. This module mitigates
the most common ones by adding randomized delays and organic mouse
movement whenever a page is opened or navigated to.

## How websites detect bots/AI (and what this module addresses)

### 1. Instant page-load actions (no human reads that fast)
**Signal:** A form is filled or a button is clicked within milliseconds
of the page finishing loading — no human reads a page that fast.
**Mitigation:** `human_settle()` waits a randomized 0–18 seconds before
any interaction, with the mouse crawling across the page during the wait
so the browser records real mouse activity in its behavioral analytics.

### 2. Perfectly linear mouse paths
**Signal:** `page.mouse.move(x, y)` jumps the cursor in a straight line
or instantly teleports — real human mouse movement has curves, jitter,
overshoot, and micro-corrections.
**Mitigation:** `crawl_page()` generates Bezier-curved paths between
random visible elements with speed variation and occasional pauses.

### 3. Zero mouse movement / no hover events
**Signal:** The page records zero `mousemove`, `mouseenter`, or `mouseover`
events before a click — a human always moves the mouse before clicking.
**Mitigation:** `crawl_page()` fires continuous mouse movement events
across the viewport during the settle delay.

### 4. Identical timing across runs
**Signal:** Every run takes exactly the same number of seconds between
steps — deterministic timing is a dead giveaway.
**Mitigation:** All delays use `random.uniform()` with wide ranges so
no two runs look the same.

### 5. Headless browser fingerprint
**Signal:** `navigator.webdriver === true`, missing Chrome properties,
or the CDP runtime is detectable.
**Mitigation:** This module does NOT address fingerprinting — that is
handled at the browser-launch level in `src/auth/context.py` (headed
mode, persistent profile, real extension). This module only addresses
*behavioral* signals.

### 6. No scroll behavior
**Signal:** A page is interacted with without ever being scrolled —
humans scroll to find things.
**Mitigation:** `crawl_page()` includes occasional gentle scroll
movements mixed into the mouse path.

### Criteria this module does NOT cover (flagged for future work)
- Canvas/WebGL fingerprint randomization (needs launch-level args)
- `navigator.webdriver` patching (needs page.add_init_script)
- Viewport-size jitter across runs (currently pinned in context.py,
  which is correct for fingerprint stability — changing it would
  increase detection risk, not reduce it)
"""

from __future__ import annotations

import math
import random
import time
from typing import Any

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# The user-requested range: 0 to 15-18 seconds of randomized settle time
# whenever a page is opened. We use 0–16 as the default (midpoint of the
# 15–18 range the user mentioned), configurable per call.
DEFAULT_SETTLE_MIN_S = 0.0
DEFAULT_SETTLE_MAX_S = 16.0

# Mouse crawl parameters
DEFAULT_CRAWL_POINTS = 8  # how many elements to hover between
DEFAULT_HOVER_MIN_MS = 200
DEFAULT_HOVER_MAX_MS = 1500
DEFAULT_MOVE_STEPS = 25  # interpolation steps per Bezier segment
DEFAULT_MOVE_STEP_DELAY_MS = 8  # ms between interpolation steps

# Occasional scroll during crawl
DEFAULT_SCROLL_CHANCE = 0.3
DEFAULT_SCROLL_MIN_PX = 50
DEFAULT_SCROLL_MAX_PX = 300


# ---------------------------------------------------------------------------
# Low-level mouse movement primitives
# ---------------------------------------------------------------------------


def _bezier_point(
    t: float,
    p0: tuple[float, float],
    p1: tuple[float, float],
    p2: tuple[float, float],
    p3: tuple[float, float],
) -> tuple[float, float]:
    """Cubic Bezier point at parameter t in [0, 1].

    Uses a cubic Bezier with two control points derived from p1/p2
    so the curve is organic rather than a straight line.
    """
    u = 1 - t
    x = (u**3) * p0[0] + 3 * (u**2) * t * p1[0] + 3 * u * (t**2) * p2[0] + (t**3) * p3[0]
    y = (u**3) * p0[1] + 3 * (u**2) * t * p1[1] + 3 * u * (t**2) * p2[1] + (t**3) * p3[1]
    return (x, y)


def _human_move(
    page,
    start: tuple[float, float],
    end: tuple[float, float],
    *,
    steps: int = DEFAULT_MOVE_STEPS,
    step_delay_ms: int = DEFAULT_MOVE_STEP_DELAY_MS,
) -> None:
    """Move the mouse from `start` to `end` along a curved path with jitter.

    Generates two random control points offset perpendicular to the
    start→end line so each movement has a unique arc — never a straight
    line. Adds small random jitter to each interpolated point so even
    the micro-movements look organic.
    """
    # Midpoint and direction
    mid_x = (start[0] + end[0]) / 2
    mid_y = (start[1] + end[1]) / 2
    dx = end[0] - start[0]
    dy = end[1] - start[1]
    length = math.hypot(dx, dy) or 1.0

    # Perpendicular offset for control points (randomized arc)
    perp_x = -dy / length
    perp_y = dx / length
    arc1 = random.uniform(-length * 0.3, length * 0.3)
    arc2 = random.uniform(-length * 0.3, length * 0.3)

    p0 = start
    p1 = (mid_x + perp_x * arc1, mid_y + perp_y * arc1)
    p2 = (mid_x + perp_x * arc2, mid_y + perp_y * arc2)
    p3 = end

    for i in range(1, steps + 1):
        t = i / steps
        x, y = _bezier_point(t, p0, p1, p2, p3)
        # micro-jitter: ±2px so even interpolated points aren't perfectly smooth
        x += random.uniform(-2, 2)
        y += random.uniform(-2, 2)
        try:
            page.mouse.move(x, y)
        except Exception:
            return
        if step_delay_ms > 0:
            page.wait_for_timeout(step_delay_ms)


def _get_random_visible_points(page, count: int) -> list[tuple[float, float]]:
    """Return up to `count` random (x, y) coordinates within the viewport,
    preferring coordinates near visible elements so hover events fire on
    real DOM nodes (not empty space).
    """
    viewport = page.viewport_size or {"width": 1440, "height": 900}
    w, h = viewport["width"], viewport["height"]

    # Try to find visible elements to hover near; fall back to random points
    try:
        boxes: list[dict[str, float]] = page.evaluate(
            """(count) => {
                const els = document.querySelectorAll(
                    'a, button, p, h1, h2, h3, span, div, li, img, label, input'
                );
                const visible = [];
                for (const el of els) {
                    const r = el.getBoundingClientRect();
                    if (r.width > 10 && r.height > 10 && r.top > 0 && r.left > 0
                        && r.bottom < window.innerHeight && r.right < window.innerWidth) {
                        visible.push({
                            x: r.x + r.width / 2,
                            y: r.y + r.height / 2,
                        });
                        if (visible.length >= count * 3) break;
                    }
                }
                return visible;
            }""",
            count,
        )
    except Exception:
        boxes = []

    points: list[tuple[float, float]] = []
    if boxes:
        sample_size = min(count, len(boxes))
        chosen = random.sample(boxes, sample_size)
        for b in chosen:
            x = b["x"] + random.uniform(-5, 5)
            y = b["y"] + random.uniform(-5, 5)
            points.append((x, y))
    while len(points) < count:
        points.append((random.uniform(50, w - 50), random.uniform(50, h - 50)))
    return points


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def crawl_page(
    page,
    *,
    duration_s: float,
    num_points: int = DEFAULT_CRAWL_POINTS,
    hover_min_ms: int = DEFAULT_HOVER_MIN_MS,
    hover_max_ms: int = DEFAULT_HOVER_MAX_MS,
    scroll_chance: float = DEFAULT_SCROLL_CHANCE,
    rng: random.Random | None = None,
) -> int:
    """Move the mouse organically across the page for `duration_s` seconds.

    Picks random visible elements, moves the cursor to each along a
    curved Bezier path with jitter, hovers briefly, and occasionally
    scrolls. This fires real `mousemove`/`mouseover`/`mouseenter` events
    that behavioral-analytics scripts look for.

    Returns the number of hover points visited (useful for logging).
    """
    r = rng or random
    deadline = time.monotonic() + duration_s
    visited = 0

    viewport = page.viewport_size or {"width": 1440, "height": 900}
    current_pos: tuple[float, float] = (
        r.uniform(100, max(200, viewport["width"] - 100)),
        r.uniform(50, 150),
    )
    try:
        page.mouse.move(current_pos[0], current_pos[1])
    except Exception:
        pass

    while time.monotonic() < deadline:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break

        points = _get_random_visible_points(page, num_points)
        r.shuffle(points)

        for pt in points:
            if time.monotonic() >= deadline:
                break

            _human_move(page, current_pos, pt)
            current_pos = pt
            visited += 1

            hover_ms = r.randint(hover_min_ms, hover_max_ms)
            page.wait_for_timeout(hover_ms)

            if r.random() < scroll_chance:
                scroll_px = r.randint(DEFAULT_SCROLL_MIN_PX, DEFAULT_SCROLL_MAX_PX)
                try:
                    page.mouse.wheel(0, scroll_px)
                except Exception:
                    pass
                page.wait_for_timeout(r.randint(200, 600))

    return visited


def human_settle(
    page,
    *,
    min_s: float = DEFAULT_SETTLE_MIN_S,
    max_s: float = DEFAULT_SETTLE_MAX_S,
    logger: Any = None,
    run_id: str | None = None,
    rng: random.Random | None = None,
) -> dict[str, Any]:
    """Wait a randomized 0–`max_s` seconds with the mouse crawling the page.

    Call this whenever a page is opened or navigated to, before any
    interaction. The delay is randomized so no two runs have identical
    timing, and the mouse is actively moving during the wait so the page
    records real behavioral signals.

    Returns a dict with the delay and crawl stats for logging.
    """
    r = rng or random
    delay = r.uniform(min_s, max_s)

    if delay <= 0:
        return {"delay_s": 0.0, "hover_points": 0}

    start = time.monotonic()
    hover_points = crawl_page(page, duration_s=delay, rng=rng)
    actual = time.monotonic() - start

    result: dict[str, Any] = {
        "delay_s": round(actual, 2),
        "hover_points": hover_points,
    }

    if logger is not None and run_id is not None:
        from src.observability.logging import log_event

        log_event(
            logger,
            "human_settle",
            run_id=run_id,
            duration=actual,
            message=f"Settled for {actual:.1f}s, hovered {hover_points} points",
            **result,
        )

    return result


def human_click(
    page,
    selector: str,
    *,
    pre_hover_s: float = 1.0,
    timeout_ms: int = 10_000,
) -> None:
    """Move the mouse to an element along a curved path, hover briefly,
    then click — instead of teleporting the cursor and clicking instantly.

    `pre_hover_s` is a short randomized hover (0.5–1.5× the given value)
    before the click, simulating a human reading the button before
    pressing it.
    """
    r = random
    locator = page.locator(selector).first

    try:
        locator.wait_for(state="visible", timeout=timeout_ms)
        box = locator.bounding_box()
    except Exception:
        locator.click()
        return

    if box is None:
        locator.click()
        return

    target_x = box["x"] + box["width"] / 2 + r.uniform(-3, 3)
    target_y = box["y"] + box["height"] / 2 + r.uniform(-3, 3)

    viewport = page.viewport_size or {"width": 1440, "height": 900}
    start_x = r.uniform(max(0, target_x - 200), min(viewport["width"], target_x + 200))
    start_y = r.uniform(max(0, target_y - 200), min(viewport["height"], target_y + 200))

    _human_move(page, (start_x, start_y), (target_x, target_y))

    hover_ms = int(pre_hover_s * 1000 * r.uniform(0.5, 1.5))
    page.wait_for_timeout(hover_ms)

    locator.click()


def human_hover_locator(
    locator, *, pre_hover_s: float = 0.4, rng: random.Random | None = None
) -> None:
    """Scroll to, hover, and briefly pause over a control before acting on
    it — so a real scroll + mousemove/hover precedes every field
    interaction (bot detectors flag clicks with no preceding hover). All
    steps are best-effort; a detached/off-screen control never raises."""
    r = rng or random
    try:
        locator.scroll_into_view_if_needed(timeout=3000)
    except Exception:
        pass
    try:
        locator.hover(timeout=3000)
    except Exception:
        pass
    try:
        locator.page.wait_for_timeout(int(pre_hover_s * 1000 * r.uniform(0.5, 1.5)))
    except Exception:
        pass


def human_fill_locator(
    locator,
    text: str,
    *,
    per_char_min_ms: int = 25,
    per_char_max_ms: int = 110,
    rng: random.Random | None = None,
) -> None:
    """Hover, focus, clear, then type `text` one character at a time with
    randomized cadence — instead of the instant `fill()` that reads as a
    bot. Falls back to a direct fill if keystroke typing breaks."""
    r = rng or random
    human_hover_locator(locator, rng=r)
    page = locator.page
    try:
        locator.click(timeout=5000)
    except Exception:
        pass
    try:
        locator.fill("")  # clear any prefill before typing
    except Exception:
        pass
    for char in text:
        try:
            page.keyboard.type(char)
        except Exception:
            try:
                locator.fill(text)  # typing broke mid-way - set it directly
            except Exception:
                pass
            return
        if char in " .,@-\n":
            delay = r.uniform(80, 220)
        else:
            delay = r.uniform(per_char_min_ms, per_char_max_ms)
        try:
            page.wait_for_timeout(int(delay))
        except Exception:
            return


def human_type(
    page,
    selector: str,
    text: str,
    *,
    per_char_min_ms: int = 30,
    per_char_max_ms: int = 120,
) -> None:
    """Type text one character at a time with randomized delay between
    keystrokes, instead of `fill()` which sets the value instantly.

    Humans type at variable speeds with occasional pauses (especially
    around spaces and punctuation). This function simulates that.
    """
    r = random
    locator = page.locator(selector).first
    locator.click()

    for char in text:
        page.keyboard.type(char)
        if char in (" ", ".", ",", "\n"):
            delay = r.uniform(80, 250)
        else:
            delay = r.uniform(per_char_min_ms, per_char_max_ms)
        page.wait_for_timeout(int(delay))
