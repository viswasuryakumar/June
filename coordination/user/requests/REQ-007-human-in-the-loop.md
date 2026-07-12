---
id: REQ-007
status: proposed
priority: high
title: Human-in-the-loop ticket and resume flow
spec_refs: ["EPIC 7"]
---

# REQ-007 — Human-in-the-loop ticket and resume flow

## Goal

Let a human resolve login, CAPTCHA, approval, and unknown-question blockers and resume the same run.

## Why

Safe automation requires explicit handoff instead of guessing or abandoning recoverable work.

## Required behavior

- Persist tickets and expose their evidence and required action.
- Notify through the configured channel and accept a human response.
- Resume held work or reconstruct it from persisted state.
- Store approved reusable answers with provenance.

## Acceptance criteria

- A CAPTCHA ticket can be resolved in an open window and the run continues.
- An unknown question round-trips to a human and its approved answer is reusable.
- Expired holds retain enough state for a safe later resume.

## Constraints and safety requirements

- Follow Epic 7 ticket, timeout, and answer-learning rules.
- Never learn or reuse an answer without explicit approval.

## Out of scope

- Automatically solving CAPTCHA or authentication challenges.
- Treating notification delivery as task resolution.

## Examples or evidence

The existing login ticket helper is a temporary interface to consolidate.
