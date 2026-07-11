"""Selector registry loading + selector-miss detector (spec §3.3, T1.4 / S1.4.3).

All CSS/XPath selectors live in selectors/jobright.yaml keyed by semantic
name (e.g. "login.email_input"). Modules should resolve locators through
`resolve_locator()` rather than hardcoding raw selector strings, so that
any miss is uniformly snapshotted and raised as `SelectorBroken` (which
routes to a HITL ticket of kind 'selector_broken' upstream).
"""

from __future__ import annotations

from pathlib import Path

import yaml

from src.contracts.exceptions import SelectorBroken
from src.observability.snapshot import snapshot

DEFAULT_SELECTORS_PATH = Path("selectors/jobright.yaml")


class SelectorRegistry:
    """Flat semantic-key -> selector-string lookup, loaded from YAML.

    The YAML is a nested mapping (e.g. `login: {email_input: "#email"}`)
    and keys are addressed dotted ("login.email_input").
    """

    def __init__(self, mapping: dict):
        self._mapping = mapping

    @classmethod
    def load(cls, path: str | Path = DEFAULT_SELECTORS_PATH) -> SelectorRegistry:
        p = Path(path)
        raw = yaml.safe_load(p.read_text()) if p.exists() else {}
        return cls(raw or {})

    def get(self, key: str) -> str:
        node: object = self._mapping
        for part in key.split("."):
            if not isinstance(node, dict) or part not in node:
                raise KeyError(f"selector key {key!r} not found in registry")
            node = node[part]
        if not isinstance(node, str):
            raise KeyError(f"selector key {key!r} does not resolve to a string selector")
        return node


def resolve_locator(
    page,
    registry: SelectorRegistry,
    key: str,
    *,
    run_id: str,
    timeout_ms: int = 5000,
):
    """Resolve a semantic selector key to a Playwright Locator, waiting for
    it to become visible. On any failure (unknown key or timeout), captures
    a snapshot and raises SelectorBroken with that context attached.

    Sync Playwright API only (matches the sync usage elsewhere in Epic 1's
    scaffolding/tests); async executor code can build an equivalent async
    variant against the same SelectorRegistry.
    """
    try:
        selector = registry.get(key)
        locator = page.locator(selector)
        locator.wait_for(state="visible", timeout=timeout_ms)
        return locator
    except Exception as exc:
        snapshot_path = None
        try:
            result = snapshot(page, f"selector-broken-{key}", run_id)
            snapshot_path = result.screenshot_path
        except Exception:
            snapshot_path = None
        page_url = None
        try:
            page_url = page.url
        except Exception:
            page_url = None
        raise SelectorBroken(
            key,
            page_url=page_url,
            snapshot_path=snapshot_path,
            original_error=str(exc),
        ) from exc
