---
id: REQ-005
status: proposed
priority: high
title: Truthful per-job resume tailoring
spec_refs: ["EPIC 5"]
---

# REQ-005 — Truthful per-job resume tailoring

## Goal

Create a tailored resume variant for each selected job without changing the approved master resume.

## Why

Relevant truthful emphasis should improve application quality without fabricating qualifications.

## Required behavior

- Start from the approved master resume and selected job description.
- Save a distinct variant per job and produce a readable diff.
- Escalate unsupported or suspicious additions for human review.

## Acceptance criteria

- The master resume remains unchanged.
- Every added claim is supported by the profile or master resume.
- A local archived variant and diff exist for each successful tailoring.
- Failures produce an actionable human-review result rather than silent fallback.

## Constraints and safety requirements

- Follow Epic 5 fabrication and fallback rules.
- Do not upload or submit a variant without the configured approval policy.

## Out of scope

- Final application submission.
- Inventing skills, employers, dates, education, or achievements.

## Examples or evidence

None yet; begin with fixture-backed dry runs before live JobRight use.
