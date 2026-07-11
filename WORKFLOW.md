# WORKFLOW.md

This file defines the standard workflow for every agent operating in this repository.

## 1. Before Starting a Task
- Read AGENTS.md.
- Read the relevant task specification.
- Review `coordination/WORKERS.md`, `coordination/claims/`, and the appropriate lane history (`PROGRESS.md` or `review.md`) to see what is owned, done, in progress, or blocked.
- Confirm the task scope and expected output.
- Create a task claim before making edits. Claims must list the exact files or directories to be changed and must not overlap another active claim.

## 2. During Execution
- Break the task into small steps.
- Follow the smallest safe implementation path.
- Keep contracts, selectors, and configuration centralized.
- Avoid introducing unrelated changes.
- If a blocker appears, record it immediately and continue with the next safe step if possible.
- Use a separate Git branch and worktree for each concurrent writer. A shared checkout is read-only unless one designated worker is the sole writer.
- Treat shared contracts, configuration, selectors, and fixtures as integration surfaces: only their active claimant edits them, and downstream workers consume the verified handoff.

### Automated 15-minute round

1. Claude Coordinator performs preflight and selects at most one eight-minute task for Developer and one for Bug-fixer.
2. The two workers run concurrently in separate worktrees. At nine minutes the supervisor records a checkpoint request; at ten minutes it terminates unfinished work.
3. Codex reviews completed commits as they arrive. It rejects out-of-claim, uncommitted, unverified, or incomplete results.
4. Codex applies each approved commit without committing, reruns its single bounded verification command, adds per-task evidence under `coordination/history/`, commits the integrated result, and pushes `main` sequentially.
5. The supervisor records the round under `runs/devloop/` and waits for the next fixed boundary. Early completion never advances the cadence.

## 3. Documentation Requirements
After every completed step, the agent must:
- Claude updates `PROGRESS.md` with implementation/test outcomes and evidence; Codex never edits `PROGRESS.md`
- Codex updates `review.md` with review activity, findings, correction requests, and E2E evidence
- Codex maintains `WORKFLOW.md` and `jobright-automation-spec.md`; Claude records proposed changes or implementation deviations in `PROGRESS.md`
- add or revise other relevant documentation only within the agent's assigned lane
- note the method used, problems encountered, and how they were resolved

## 4. Progress Log Format
Each Claude-authored progress note should include:
- date/time
- agent name
- task or epic
- summary of work completed
- method followed
- problems faced
- how the issue was overcome
- status
- verification notes

## 5. Task Ownership by Agent Type
- Foundation agent: scaffold repository structure, shared contracts, configs, and logging.
- Auth agent: manage browser context, login flow, and session persistence.
- Discovery agent: ingest jobs, enrich them, and persist job records.
- Selection agent: apply filtering, ranking, and daily quota rules.
- Resume agent: tailor resumes and validate output.
- Executor agent: drive application flow and escalate to HITL when required.
- HITL agent: manage tickets, approvals, and human handoff.
- Tracker/reporting agent: maintain state, audit logs, and reporting.
- Orchestrator agent: coordinate the full pipeline and ensure safety rails.

These are runtime-component ownership areas, not contributor identities. Current contributor lanes and epic assignments are recorded in `coordination/WORKERS.md`; an assignment becomes active only when it has a corresponding claim.

## 6. Handoff and Integration

- A handoff includes the claim ID, changed files, public interfaces affected, verification commands/results, known risks, and required follow-up.
- The worker closes its claim after publishing the handoff. Closed claims remain in place as short coordination evidence; detailed history belongs in `PROGRESS.md` or `review.md`.
- The integration owner checks that claims do not overlap, required tests pass, and public contracts remain compatible before merging.
- Failed or incomplete work is handed off as blocked; it is never represented as complete solely because code exists.
- Timed-out work retains its checkpoint branch and is decomposed into a smaller continuation for a later round.
- No agent force-pushes. A rejected integration or remote update blocks the task until a fresh preflight and review.

## 7. Definition of Done
A task is done only when:
- implementation is complete
- verification was performed
- documentation was updated
- the progress log contains a current entry
- the handoff is clear for the next agent
