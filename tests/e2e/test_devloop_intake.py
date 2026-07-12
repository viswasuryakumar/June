from __future__ import annotations

import json
from pathlib import Path

import pytest
from devloop.intake import (
    IntakeError,
    RequestState,
    load_feedback,
    load_intake,
    load_request,
    write_state,
)
from devloop.models import Task
from devloop.supervisor import DevLoopConfig, DevLoopError, DevLoopSupervisor


def request_text(
    request_id: str = "REQ-100", status: str = "approved", priority: str = "high"
) -> str:
    return f"""---
id: {request_id}
status: {status}
priority: {priority}
title: Test request
spec_refs: ["EPIC 5"]
---

# {request_id} — Test request

## Goal
Deliver one visible behavior.

## Why
The user requested it.

## Required behavior
- Add the behavior.

## Acceptance criteria
- The behavior is observable.
- Regression coverage passes.

## Constraints and safety requirements
- Preserve safety gates.

## Out of scope
- Unrelated refactors.

## Examples or evidence
None yet.
"""


def feedback_text(request_id: str = "REQ-100", verdict: str = "changes-required") -> str:
    return f"""---
request_id: {request_id}
verdict: {verdict}
---

# Feedback — {request_id}

## What worked
The base behavior exists.

## What did not work
One acceptance condition is missing.

## Expected correction
Add the missing condition.

## Evidence or run IDs
run-1
"""


def write_request(repo: Path, name: str, content: str) -> Path:
    path = repo / "coordination" / "user" / "requests" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def write_feedback(repo: Path, name: str, content: str) -> Path:
    path = repo / "coordination" / "user" / "feedback" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def sourced_task(request_id: str) -> Task:
    return Task(
        task_id="request-chunk",
        role="developer",
        title="Request chunk",
        prompt="Implement one chunk.",
        files=("src/example.py",),
        acceptance_criteria=("The chunk passes.",),
        verification_command="git diff --check",
        source_type="request",
        source_id=request_id,
    )


def test_request_parser_requires_detailed_sections(tmp_path: Path) -> None:
    path = write_request(tmp_path, "REQ-100-test.md", request_text())
    request = load_request(path)
    assert request.request_id == "REQ-100"
    assert request.acceptance_criteria == (
        "The behavior is observable.",
        "Regression coverage passes.",
    )

    path.write_text(request_text().replace("## Out of scope", "## Removed"), encoding="utf-8")
    with pytest.raises(IntakeError, match="Out of scope"):
        load_request(path)
    path.write_text(request_text().replace("title:", "worker: claude\ntitle:"), encoding="utf-8")
    with pytest.raises(IntakeError, match="unsupported"):
        load_request(path)


def test_feedback_has_only_user_verdict_fields(tmp_path: Path) -> None:
    path = write_feedback(tmp_path, "REQ-100-01.md", feedback_text())
    assert load_feedback(path).verdict == "changes-required"
    path.write_text(
        feedback_text().replace("verdict:", "worker: claude\nverdict:"), encoding="utf-8"
    )
    with pytest.raises(IntakeError, match="exactly"):
        load_feedback(path)


def test_only_approved_requests_are_eligible_and_feedback_reorders(tmp_path: Path) -> None:
    write_request(tmp_path, "REQ-100-low.md", request_text("REQ-100", "approved", "low"))
    write_request(tmp_path, "REQ-101-critical.md", request_text("REQ-101", "approved", "critical"))
    write_request(tmp_path, "REQ-102-proposed.md", request_text("REQ-102", "proposed", "critical"))
    write_feedback(tmp_path, "REQ-100-01.md", feedback_text("REQ-100", "changes-required"))

    intake = load_intake(tmp_path)

    assert [item.request_id for item in intake.eligible_requests()] == ["REQ-100", "REQ-101"]


def test_accepted_feedback_closes_request_without_agent_edit(tmp_path: Path) -> None:
    write_request(tmp_path, "REQ-100-test.md", request_text())
    feedback = write_feedback(tmp_path, "REQ-100-01.md", feedback_text(verdict="accepted"))

    intake = load_intake(tmp_path)

    assert intake.eligible_requests() == []
    assert intake.effective_lifecycle("REQ-100") == "accepted"
    assert feedback.read_text(encoding="utf-8") == feedback_text(verdict="accepted")


def test_automation_state_is_separate_from_user_request(tmp_path: Path) -> None:
    request_path = write_request(tmp_path, "REQ-100-test.md", request_text())
    original = request_path.read_text(encoding="utf-8")
    write_state(
        tmp_path,
        RequestState(
            request_id="REQ-100",
            lifecycle="delivered",
            child_task_ids=["request-chunk"],
            integrated_commits=["abc123"],
        ),
    )

    intake = load_intake(tmp_path)

    assert intake.effective_lifecycle("REQ-100") == "delivered"
    assert request_path.read_text(encoding="utf-8") == original


def test_task_source_must_be_approved_and_highest_priority(tmp_path: Path) -> None:
    write_request(tmp_path, "REQ-100-low.md", request_text("REQ-100", "approved", "low"))
    write_request(tmp_path, "REQ-101-high.md", request_text("REQ-101", "approved", "high"))
    intake = load_intake(tmp_path)

    with pytest.raises(DevLoopError, match="higher-priority"):
        DevLoopSupervisor.validate_task_sources([sourced_task("REQ-100")], intake)
    DevLoopSupervisor.validate_task_sources([sourced_task("REQ-101")], intake)


def test_seeded_requests_cover_remaining_work_and_start_proposed() -> None:
    repo = Path(__file__).parents[2]
    intake = load_intake(repo)
    assert set(intake.requests) >= {
        "REQ-005",
        "REQ-006",
        "REQ-007",
        "REQ-008",
        "REQ-009",
        "REQ-010",
    }
    assert all(
        intake.requests[item].status == "proposed"
        for item in ("REQ-005", "REQ-006", "REQ-007", "REQ-008", "REQ-009", "REQ-010")
    )


def test_retrospective_threshold_and_window_marker(tmp_path: Path) -> None:
    runtime = tmp_path / "runs" / "devloop"
    for index in range(8):
        path = runtime / f"20260712T000{index:02d}Z" / "round.json"
        path.parent.mkdir(parents=True)
        path.write_text(
            json.dumps(
                {
                    "results": [{"status": "completed"}],
                    "reviews": [{"approved": True}],
                }
            ),
            encoding="utf-8",
        )
    supervisor = DevLoopSupervisor(
        DevLoopConfig(repo=tmp_path, runtime_dir=runtime, push=False),
        planner=lambda _round_id: [],
    )

    proposal = supervisor._maybe_write_improvement_proposal()

    assert proposal is not None
    assert "status: proposed" in proposal.read_text(encoding="utf-8")
    assert "Average completed-worker seconds" in proposal.read_text(encoding="utf-8")
    assert supervisor._maybe_write_improvement_proposal() is None
