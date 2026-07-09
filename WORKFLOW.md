# WORKFLOW.md

This file defines the standard workflow for every agent operating in this repository.

## 1. Before Starting a Task
- Read AGENTS.md.
- Read the relevant task specification.
- Review PROGRESS.md to see what is already done, in progress, or blocked.
- Confirm the task scope and expected output.

## 2. During Execution
- Break the task into small steps.
- Follow the smallest safe implementation path.
- Keep contracts, selectors, and configuration centralized.
- Avoid introducing unrelated changes.
- If a blocker appears, record it immediately and continue with the next safe step if possible.

## 3. Documentation Requirements
After every completed step, the agent must:
- update PROGRESS.md with the outcome and evidence
- add or revise any relevant documentation in the repository
- note the method used, problems encountered, and how they were resolved

## 4. Progress Log Format
Each progress note should include:
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

## 6. Definition of Done
A task is done only when:
- implementation is complete
- verification was performed
- documentation was updated
- the progress log contains a current entry
- the handoff is clear for the next agent
