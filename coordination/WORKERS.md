# Worker registry

This file separates contributor workers from runtime pipeline components. Update it when a claim starts or closes; use claim files for exact edit scope.

## Contributor workers

| Worker | Lane | Owned artifacts | Handoff channel |
|---|---|---|---|
| Claude | Implementation and focused tests | `src/`, `config/`, `selectors/`, existing flat tests, `tests/unit/`, `tests/component/`, shared fixtures, `PROGRESS.md` | Reads findings from `review.md`; publishes implementation evidence in `PROGRESS.md` |
| Codex (sol) | Review, specification, E2E acceptance, integration coordination | `review.md`, `WORKFLOW.md`, `jobright-automation-spec.md`, `tests/e2e/`, this registry | Reads implementation evidence from `PROGRESS.md`; publishes findings in `review.md` |

`AGENTS.md` and contributor-specific instruction files are shared-contract documents. Codex changes them only when the cross-agent contract needs correction.

## Runtime component status

| Epic | Component | Responsibility | Current state | Active claim |
|---|---|---|---|---|
| 1 | Foundation | Contracts, configuration, selectors, logging, and tracker interfaces | Implemented; integration continues | None recorded |
| 2 | Auth/Session | Browser context, login, persistent session, relogin, auth health check | Implemented with open verification gaps | None recorded |
| 3 | Discovery | Feed ingestion, extraction, enrichment, deduplication, persistence | Implemented with open review findings | None recorded |
| 4 | Selection | Filtering, scoring, ranking, and quota enforcement | In progress in current worktree; formal claim required before further concurrent edits | None recorded |
| 5 | Resume | Resume tailoring and fabrication validation | Not started | None |
| 6 | Executor and ATS adapters | Application automation, form filling, submission, and ATS-specific behavior | Not started | None |
| 7 | HITL | Tickets, approvals, notification, pause/resume | Partial login bridge only | None |
| 8 | Tracker/Reporting | Persistent state, audit history, metrics, and reports | Foundation repository only | None |
| 9 | Orchestrator | Pipeline scheduling, pacing, timeouts, quotas, and safety rails | Stub only | None |

The table reports product status, not proof of completion. The specification, tests, `PROGRESS.md`, and `review.md` provide the evidence.
