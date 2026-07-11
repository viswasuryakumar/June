"""Executable checks for implementation/spec gaps found by continuous review.

Known gaps are strict xfails: they document today's state without making the
whole suite red, and turn into an XPASS failure when implementation changes so
the finding and test must be consciously resolved.
"""

from __future__ import annotations

from importlib.util import find_spec
from pathlib import Path


# REV-001 resolved: src/auth/check.py now ships the `python -m src.auth.check`
# entrypoint the Epic 2 DoD names (see PROGRESS.md for the fix + evidence).
def test_auth_check_entrypoint_named_by_spec_exists() -> None:
    assert find_spec("src.auth.check") is not None


def test_review_document_tracks_current_findings() -> None:
    review = (Path(__file__).parents[1] / "review.md").read_text(encoding="utf-8")
    for finding_id in ("REV-001", "REV-002", "REV-003", "REV-004"):
        assert finding_id in review
