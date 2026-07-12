# Worker registry

This file separates contributor workers from runtime pipeline components. Update it when a claim starts or closes; use claim files for exact edit scope.

## Four-agent development loop

| Worker | Lane | Owned artifacts | Handoff channel |
|---|---|---|---|
| Claude Coordinator | Planning and dispatch | Task decomposition, round state, and non-overlapping claims | Dispatches one bounded task to each implementation worker every round |
| Developer | Feature implementation | Exact files claimed for one dependency-ready feature increment | Commits its branch and returns structured evidence to Codex |
| Bug-fixer | Defect repair and bounded audit | Exact files claimed for one review finding or audit | Commits a verified fix, or returns an evidence-backed finding/no-issue result |
| Codex (sol) | Independent review and integration | `review.md`, `WORKFLOW.md`, `jobright-automation-spec.md`, `tests/e2e/`, this registry | Approves/rejects results; sequentially integrates and pushes accepted work |

The user owns `coordination/user/`: product requests, acceptance feedback, durable decisions, and
workflow-proposal decisions. No agent may modify those files. Automation owns derived state and
evidence under `coordination/state/`, `coordination/history/`, and `coordination/proposals/`.

`AGENTS.md` and contributor-specific instruction files are shared-contract documents. Codex changes them only when the cross-agent contract needs correction.

All four roles participate in a 15-minute round. Developer and Bug-fixer run concurrently for no more
than ten minutes. Claude Coordinator waits after early completion; Codex reviews results as they arrive
and owns the only permitted merge/push path.

## Runtime component status

| Epic | Component | Responsibility | Current state | Active claim |
|---|---|---|---|---|
| 1 | Foundation | Contracts, configuration, selectors, logging, and tracker interfaces | Implemented; integration continues | None recorded |
| 2 | Auth/Session | Browser context, login, persistent session, relogin, auth health check | Implemented with open verification gaps | None recorded |
| 3 | Discovery | Feed ingestion, extraction, enrichment, deduplication, persistence | Implemented with live-site verification gaps | None recorded |
| 4 | Selection | Filtering, scoring, ranking, and quota enforcement | Implemented; user acceptance pending through future product requests | None recorded |
| 5 | Resume | Resume tailoring and fabrication validation | Not started | None |
| 6 | Executor and ATS adapters | Application automation, form filling, submission, and ATS-specific behavior | Not started | None |
| 7 | HITL | Tickets, approvals, notification, pause/resume | Partial login bridge only | None |
| 8 | Tracker/Reporting | Persistent state, audit history, metrics, and reports | Foundation repository only | None |
| 9 | Orchestrator | Pipeline scheduling, pacing, timeouts, quotas, and safety rails | Stub only | None |

The table reports product status, not proof of completion. The specification, tests, `PROGRESS.md`, and `review.md` provide the evidence.
