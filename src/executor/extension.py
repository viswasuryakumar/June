"""JobRight browser-extension integration (spec S6.2.2 / T2.3).

Two jobs:

1. :func:`find_installed_jobright_extension` — locate the unpacked
   "Jobright Autofill" extension inside the user's local Chrome/Edge
   profiles so the automation's persistent context can load it via
   ``BrowserContextConfig(extension_path=...)``. Found live on this
   machine 2026-07-14: id ``odcnpipkhjegpefkfplmedhmkmmhmoko`` v1.16.0
   under Chrome "Profile 1"/"Profile 3".
2. :func:`trigger_extension_autofill` — on an external ATS page, find
   the extension's injected autofill affordance and click it, then wait
   for it to change field values. The injected UI has not been observed
   live yet, so detection tries a small set of candidate selectors and
   reports honestly (False) when nothing actionable is present — the
   caller then falls back to native fill (the user's case 3).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from src.observability.human import human_hover_locator

JOBRIGHT_EXTENSION_ID = "odcnpipkhjegpefkfplmedhmkmmhmoko"


def _browser_extension_roots() -> list[Path]:
    local = os.environ.get("LOCALAPPDATA", "")
    if not local:
        return []
    base = Path(local)
    return [
        base / "Google" / "Chrome" / "User Data",
        base / "Microsoft" / "Edge" / "User Data",
    ]


def _looks_like_jobright(manifest_path: Path) -> bool:
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception:
        return False
    return "jobright" in str(manifest.get("name", "")).casefold()


def find_installed_jobright_extension(
    roots: list[Path] | None = None,
) -> Path | None:
    """Newest unpacked JobRight extension version dir, or None.

    Looks for `<profile>/Extensions/<JOBRIGHT_EXTENSION_ID>/<version>/`
    under every Chrome/Edge profile; falls back to manifest-name matching
    so a re-published extension id is still found.
    """
    candidates: list[tuple[str, Path]] = []
    for root in roots if roots is not None else _browser_extension_roots():
        if not root.is_dir():
            continue
        for profile_dir in root.iterdir():
            extensions_dir = profile_dir / "Extensions"
            if not extensions_dir.is_dir():
                continue
            for ext_dir in extensions_dir.iterdir():
                for version_dir in sorted(p for p in ext_dir.iterdir() if p.is_dir()):
                    manifest = version_dir / "manifest.json"
                    if not manifest.is_file():
                        continue
                    if ext_dir.name == JOBRIGHT_EXTENSION_ID or _looks_like_jobright(manifest):
                        candidates.append((version_dir.name, version_dir))
    if not candidates:
        return None
    # highest version wins (string sort is fine for dotted versions of
    # equal arity; exact enough for picking between stale copies)
    candidates.sort(key=lambda item: item[0])
    return candidates[-1][1]


# Candidate selectors for the extension's injected on-page autofill
# affordance. UNVERIFIED against the live extension UI (it has never run
# inside the automation profile yet) — first supervised run should
# replace these with observed values via selectors/jobright.yaml's
# `extension:` section, which takes precedence when present.
_INJECTED_AUTOFILL_CANDIDATES = (
    "text=/^\\s*Autofill\\s*$/",
    "#jobright-autofill-button",
    "text=/autofill with jobright/i",
    # NOTE: never use a broad "[id*='jobright'] button" candidate - on the
    # live panel it matched the Feedback button first (2026-07-15) and
    # opened a feedback dialog instead of autofilling.
)


@dataclass
class AutofillAttempt:
    triggered: bool
    changed_fields: int = 0
    detail: str = ""


def trigger_extension_autofill(
    page,
    *,
    registry=None,
    run_id: str | None = None,
    wait_ms: int = 8000,
    appear_wait_ms: int = 20_000,
) -> AutofillAttempt:
    """Find + click the extension's injected autofill control on `page`,
    then measure whether any field values changed. Returns an honest
    `triggered=False` when no affordance is found — callers fall back to
    native fill; nothing here raises for that normal case.
    """
    selectors: list[str] = []
    if registry is not None:
        try:
            selectors.append(registry.get("extension.autofill_trigger"))
        except KeyError:
            pass
    selectors.extend(_INJECTED_AUTOFILL_CANDIDATES)

    # The panel is injected by the extension's content script and can live
    # either in the page DOM (open shadow root - page.locator pierces it)
    # or inside the extension's own iframe (live-observed on Lever
    # 2026-07-15: page-level search missed it) - so search the main frame
    # first, then every subframe, extension frames foremost.
    frames = [page.main_frame]
    frames.extend(
        sorted(
            (f for f in page.frames if f is not page.main_frame),
            key=lambda f: not f.url.startswith("chrome-extension://"),
        )
    )

    # the panel (plasmo-csui#jobright-helper-plugin, open shadow root -
    # probed live 2026-07-15) mounts several seconds after page load, so
    # poll for a candidate to appear rather than checking once.
    def find_ready_candidate():
        for frame in frames:
            for selector in selectors:
                try:
                    locator = frame.locator(selector).first
                    if locator.count() and locator.is_visible():
                        return frame, selector, locator
                except Exception:
                    continue
        return None

    found = find_ready_candidate()
    waited = 0
    while found is None and waited < appear_wait_ms:
        page.wait_for_timeout(2000)
        waited += 2000
        found = find_ready_candidate()

    before = _field_value_snapshot(page)
    clicked_but_inert: AutofillAttempt | None = None
    candidates = [found] if found is not None else []
    candidates.extend(
        (frame, selector, frame.locator(selector).first)
        for frame in frames
        for selector in selectors
        if found is None or (frame, selector) != (found[0], found[1])
    )
    for frame, selector, locator in candidates:
        try:
            if not locator.count() or not locator.is_visible():
                continue
            human_hover_locator(locator)  # hover before clicking (anti-bot)
            locator.click()
        except Exception:
            continue
        where = "main frame" if frame is page.main_frame else f"frame {frame.url[:60]}"
        # autofill (and its resume upload) is not instant - poll for
        # field changes instead of a single fixed sleep, and if this
        # click changed nothing it probably hit the wrong control, so
        # keep trying the remaining candidates.
        changed = _poll_for_changes(page, before, wait_ms)
        if changed:
            _wait_for_autofill_to_finish(page)
            return AutofillAttempt(
                triggered=True, changed_fields=changed, detail=f"{selector} ({where})"
            )
        clicked_but_inert = AutofillAttempt(
            triggered=True, changed_fields=0, detail=f"{selector} ({where}, no effect)"
        )
    if clicked_but_inert is not None:
        return clicked_but_inert
    return AutofillAttempt(triggered=False, detail="no injected autofill UI found")


def _wait_for_autofill_to_finish(page, *, max_wait_ms: int = 45_000) -> None:
    """The panel shows an 'Autofilling...' progress bar while it works
    (incl. the resume upload) - verifying mid-run reads half-filled state
    (live-observed 2026-07-15 at 86%), so wait for it to clear."""
    waited = 0
    while waited < max_wait_ms:
        try:
            indicator = page.locator("text=/Autofilling/i").first
            if not indicator.count() or not indicator.is_visible():
                return
        except Exception:
            return
        page.wait_for_timeout(1500)
        waited += 1500


def _poll_for_changes(page, before: dict[str, str], wait_ms: int) -> int:
    step_ms = min(2000, max(wait_ms, 50))
    waited = 0
    changed = 0
    while waited < wait_ms or waited == 0:
        try:
            page.wait_for_timeout(step_ms)
            after = _field_value_snapshot(page)
        except Exception:
            # the apply page can close/navigate mid-poll (e.g. a
            # one-click-apply tab that submits and closes itself) - stop
            # polling instead of crashing the run.
            break
        waited += step_ms
        changed = sum(1 for k, v in after.items() if before.get(k, "") != v and v)
        if changed:
            break
    return changed


def _field_value_snapshot(page) -> dict[str, str]:
    try:
        return page.evaluate(
            """() => {
              const out = {};
              document.querySelectorAll("input, textarea, select").forEach((el, i) => {
                const key = el.name || el.id || `idx${i}`;
                out[key] = el.value || "";
              });
              return out;
            }"""
        )
    except Exception:
        return {}
