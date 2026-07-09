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
Every agent must write documentation after completing each step or task.

- Record completed work in PROGRESS.md.
- Keep long-form process guidance in WORKFLOW.md.
- Do not put detailed progress history into AGENTS.md; keep it in the dedicated progress log.

## Progress Logging Rules
Every agent must maintain the repository progress log in PROGRESS.md.

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

## Workflow Contract
Each agent must follow this workflow for every task:
1. Read the relevant spec or task description.
2. Read the current progress log and identify whether the task is already started or blocked.
3. Implement the smallest viable solution for the task.
4. Verify the result with the appropriate command, test, or inspection.
5. Update the documentation and the progress log.
6. Hand off the result clearly, including any remaining risk or next step.

## Repository Conventions
- Keep source code under src/.
- Keep configuration under config/.
- Keep selectors in selectors/.
- Keep runtime artifacts under runs/.
- Keep tests under tests/.
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

