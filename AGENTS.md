# AGENTS.md

> Read this file before doing any work in this repo. It applies to all agents — Roo Code, Claude Code, GitHub Copilot, or any other tool.

## Project Overview
This repository is for building an autonomous JobRight auto-apply pipeline. The work is organized by epics and tasks, and every agent is expected to deliver production-ready, verifiable work that fits the shared architecture.

## Repository & Links
- GitHub repository: https://github.com/viswasuryakumar/June

## Tech Stack
- Python 3.11+
- Playwright for browser automation and session management
- SQLite for local tracking and state storage
- Pydantic for shared data contracts and validation
- YAML for configuration and selector definitions
- python-dotenv and environment variables for secrets
- pytest, ruff, and black for testing and code quality

## Setup / Development Environment
- Use Python 3.11+ with a virtual environment.
- Install project dependencies using the repository package manager setup once it exists.
- Install Playwright browsers for local execution.
- Configure environment variables for JobRight credentials and any notification tokens.
- Keep browser profiles, screenshots, and run artifacts under the repo's expected runtime folders.

## Build, Test, and Run
- Prefer dry-run and safe validation modes before live automation.
- Use pytest for automated tests where available.
- Use ruff and black for linting and formatting.
- Follow the documented run entrypoints for discovery, resume, and HITL flows.

## Code Style & Conventions
- Keep source code under src/.
- Keep configuration under config/ and selectors under selectors/.
- Centralize selectors and shared contracts instead of hardcoding them in modules.
- Prefer small, testable functions and explicit interfaces.
- Never log secrets, passwords, tokens, or personal data.

## Core Operating Rules
- Read the task spec, this file, the workflow file, and the progress log before starting work.
- Prefer small, testable changes over large rewrites.
- Keep shared contracts, selectors, and configuration centralized.
- Never hardcode secrets or credentials. Use environment variables or a secure secret store.
- If a task is ambiguous, document the assumption and proceed with the smallest safe implementation.

## Documentation Requirement
Every agent must document completed work in the artifact assigned to its lane.

- Claude records implementation, unit/component-test, and shared-fixture work in `PROGRESS.md`.
- Codex does not edit `PROGRESS.md`; it records review activity, findings, corrections, and E2E evidence in `review.md`.
- Read `review.md` before starting and address or explicitly hand off any open finding in the files you own.
- Keep `review.md` as the rolling evidence-based review report; use stable finding IDs and mark findings resolved instead of deleting them.
- Keep long-form process guidance in WORKFLOW.md.
- Do not put detailed progress history into AGENTS.md; keep it in the dedicated progress log.

## Progress Logging Rules
Claude exclusively maintains the append-only repository progress log in `PROGRESS.md`. Codex reads it as implementation evidence but never edits it.

For every task, record:
- date and time
- agent name
- task or epic name
- what was done
- method followed
- problems encountered
- how the problem was overcome
- current status
- verification evidence

If a task is still in progress, add an entry marked as in-progress with the blocker and the next action.

## Multi-Agent Roles
Two independent agents operate on this repo concurrently, split by lane rather than by task slice. Each owns its lane end-to-end — including proposing and adding new ideas beyond the literal spec text where they improve the product — and neither silently edits the other's files.

- **Claude (implementation agent)** — builds and fixes the pipeline. Owns `src/`, `config/`, `selectors/`, and unit/component tests: implements features, resolves findings raised in `review.md`, and writes focused tests for individual functions, classes, modules, and bounded subsystem integrations. Existing flat `tests/test_*.py` files remain Claude-owned; new focused suites should use `tests/unit/` or `tests/component/` when practical. Claude also owns shared test fixtures in `tests/conftest.py` and `tests/fixtures/`. Records every completed step in `PROGRESS.md` with verification evidence. Reads `review.md` before starting but never writes to it. **Does not edit `WORKFLOW.md` or `jobright-automation-spec.md`** — those are Codex's exclusive lane; a workflow/spec-relevant deviation or new idea gets recorded in `PROGRESS.md` instead, for Codex to fold into the appropriate document.
- **Codex ("sol", continuous review/orchestrator agent)** — runs review cycles and owns end-to-end/acceptance testing under `tests/e2e/`. These tests exercise real user-visible workflows or multiple runtime stages through public interfaces (for example Auth → Discovery → Selection), validate acceptance criteria and failure recovery, and avoid mocking the behavior under test except at unavoidable external boundaries. Codex also owns `review.md` (review activity, evidence, findings, and corrections), `WORKFLOW.md` (shared process), and `jobright-automation-spec.md` (the living engineering spec). It reads `PROGRESS.md` as Claude's implementation record but never edits it. It verifies claims against real code/test behavior rather than trusting progress-log claims at face value. Codex does not rewrite Claude's implementation or focused tests; it reports failures and requested corrections in `review.md` for Claude to address. Updates `AGENTS.md`/`CLAUDE.md` only when the cross-agent operating contract itself genuinely needs clarification.

Rules for both:
- Read `PROGRESS.md` (and, for Codex, the spec) before starting, so you build on the other agent's latest state instead of duplicating or contradicting it.
- New ideas beyond the spec are encouraged, but must be recorded (what was added and why) — never silently expand scope without a trace.
- Stay in your lane's files. Claude sends handoffs to Codex through `PROGRESS.md`; Codex sends findings and correction requests to Claude through `review.md`.
- Testing boundary: Claude may read and run `tests/e2e/` but must not edit it; Codex may read and run Claude's unit/component tests but must not edit them. Codex requests shared-fixture changes in `review.md`, and Claude implements and records them in `PROGRESS.md`.

## Concurrent Work Safety

- Before editing, create a task claim under `coordination/claims/` using the process in `coordination/README.md`. A claim identifies one worker, one task, and the exact files or directories it may edit.
- Claims are exclusive at the file level. Do not begin work that overlaps an active claim; split the files differently or wait for a handoff.
- Each concurrently editing worker must use its own Git branch and worktree. Sharing a checkout is allowed only for read-only review or when exactly one worker is writing.
- Never merge or copy another worker's incomplete changes. The integration owner accepts a handoff only after the worker records verification evidence.
- Runtime-stage names such as Auth, Discovery, and Executor describe product components. Claude and Codex are contributor roles. Do not use the two categories interchangeably in task claims or status reports.
- `coordination/WORKERS.md` is the current worker and epic registry. It is status, not historical evidence; `PROGRESS.md` and `review.md` remain the lane-specific history.

## Workflow Contract
Each agent must follow this workflow for every task:
1. Read the relevant spec or task description.
2. Read `coordination/WORKERS.md`, active claims, and the lane-specific history to identify ownership, dependencies, and blockers.
3. Create a non-overlapping task claim before editing.
4. Implement the smallest viable solution for the task in an isolated branch/worktree when other writers are active.
5. Verify the result with the appropriate command, test, or inspection.
6. Update the documentation and the lane-specific history.
7. Hand off the result clearly, including verification evidence and any remaining risk or next step; then close the claim.

## Repository Conventions
- Keep source code under src/.
- Keep configuration under config/.
- Keep selectors in selectors/.
- Keep runtime artifacts under runs/.
- Keep Claude-owned unit/component tests under `tests/unit/`, `tests/component/`, or the existing flat `tests/test_*.py` layout; keep Codex-owned end-to-end tests under `tests/e2e/`.
- Keep changes scoped to the task; avoid unrelated refactors.

## Definition of Done
A task is only complete when all of the following are true:
- the implementation is in place
- the result is verified
- the progress note is updated
- the documentation reflects the final state
- no secrets or sensitive information are exposed

## Safety and Boundaries
- Do not touch secrets, tokens, or personal data outside the approved config flow.
- Do not edit historical progress entries; append new entries when correcting or extending information.
- If blocked, record the blocker clearly instead of silently skipping the work.
