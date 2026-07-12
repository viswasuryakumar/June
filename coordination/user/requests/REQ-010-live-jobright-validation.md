---
id: REQ-010
status: approved
priority: critical
title: Validate live JobRight authentication, selectors, and discovery
spec_refs: ["EPIC 2", "EPIC 3"]
---

# REQ-010 — Validate live JobRight authentication, selectors, and discovery

## Goal

Replace placeholder assumptions with evidence from a real JobRight account and live DOM.

## Why

Authentication and discovery are fixture-tested but cannot be trusted live while selectors and
extension authentication remain unverified.

## Required behavior

- Verify login, challenge, dashboard, feed, card, detail, and apply selectors.
- Confirm job ID, posted-time, and apply-mode extraction against real listings.
- Implement the real extension-authentication check once its UI is observed.
- Exercise persistent login and two consecutive discovery runs.

## Acceptance criteria

- No required live selector remains a placeholder.
- Login state survives restart and real challenge screens create a human ticket.
- Live discovery ingests at least 95% of visible cards with correct apply mode.
- Consecutive discovery runs do not misreport refreshed jobs as new jobs.

## Constraints and safety requirements

- Credentials remain only in ignored secret storage.
- Begin with read-only discovery; do not submit applications.
- Preserve screenshots and DOM evidence under ignored run artifacts.

## Out of scope

- Unsupervised application submission.
- Guessing selectors without live evidence.

## Examples or evidence

Current placeholder inventory is documented in `selectors/jobright.yaml` and the Epic 2/3 open gaps.
