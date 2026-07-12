---
id: REQ-008
status: proposed
priority: medium
title: Persistent tracker and useful run reports
spec_refs: ["EPIC 8"]
---

# REQ-008 — Persistent tracker and useful run reports

## Goal

Persist application history and produce a concise explanation of every automated run.

## Why

The user needs auditability, duplicate prevention, and clear evidence of outcomes.

## Required behavior

- Store jobs, transitions, tickets, attempts, reasons, and evidence persistently.
- Produce per-run and daily summaries.
- Link submitted applications to confirmation evidence.

## Acceptance criteria

- State survives process restarts.
- Every skip and failure has a machine-readable reason.
- One run report explains selected, skipped, blocked, failed, and submitted counts.
- Every submitted application links to confirmation evidence.

## Constraints and safety requirements

- Follow Epic 8 retention, redaction, and reporting requirements.
- Do not expose credentials or unnecessary personal data in logs.

## Out of scope

- Optional rich dashboards before the core persistent tracker is reliable.

## Examples or evidence

Build on the existing in-memory repository contract and observability events.
