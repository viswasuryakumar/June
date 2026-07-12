---
id: REQ-006
status: proposed
priority: high
title: Supervised application executor
spec_refs: ["EPIC 6"]
---

# REQ-006 — Supervised application executor

## Goal

Complete supported JobRight and external ATS applications safely in supervised mode.

## Why

The pipeline currently selects jobs but cannot complete applications end to end.

## Required behavior

- Support JobRight Agent apply and extension-driven external apply paths.
- Fill only answers backed by the approved profile or learned human answers.
- Verify a real success signal before recording submission.
- Pause for unknown questions, CAPTCHA, or final approval.

## Acceptance criteria

- Five supervised applications per supported path use the correct resume and answers.
- No application is marked submitted without confirmation evidence.
- CAPTCHA and ambiguous required questions create human handoffs.

## Constraints and safety requirements

- Follow Epic 6 pacing, failure, CAPTCHA, and approval rules.
- Never bypass bot protection or guess sensitive answers.

## Out of scope

- Unsupervised submission before staged acceptance is complete.
- Unsupported ATS-specific behavior not covered by an adapter.

## Examples or evidence

Use screenshots and tracker records from supervised runs.
