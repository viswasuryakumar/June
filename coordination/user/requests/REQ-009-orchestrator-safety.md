---
id: REQ-009
status: proposed
priority: high
title: Runnable pipeline orchestration and safety rails
spec_refs: ["EPIC 9"]
---

# REQ-009 — Runnable pipeline orchestration and safety rails

## Goal

Coordinate authentication, discovery, selection, resume, execution, HITL, and reporting safely.

## Why

Implemented stages are not yet connected into a production pipeline.

## Required behavior

- Run stages with timeouts, resumable state, pacing, and daily limits.
- Provide dry-run, stage-only, kill-switch, and staged-autonomy modes.
- Process application submissions sequentially.

## Acceptance criteria

- A scheduled supervised run completes within configured limits and produces a report.
- The kill switch stops an active run without falsely recording success.
- Dry-run performs no submission action.
- Staged 1, 3, and 10-job gates require review before autonomy increases.

## Constraints and safety requirements

- Follow Epic 9 safety, quota, pacing, and integration-test rules.
- Never bypass an unresolved HITL gate.

## Out of scope

- Raising autonomy before supervised acceptance evidence exists.

## Examples or evidence

Replace the current empty pipeline stub only after stage interfaces are ready.
