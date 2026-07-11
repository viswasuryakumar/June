# CLAUDE.md

> This repository uses AGENTS.md as the canonical agent instructions file.
> Read AGENTS.md, WORKFLOW.md, and PROGRESS.md before doing any work in this repo.

Claude-specific notes:
- Automated work uses three Claude roles: Coordinator plans without editing; Developer implements one bounded feature increment; Bug-fixer repairs one bounded finding or audits one component. Developer and Bug-fixer target eight minutes and stop by ten.
- Your lane is `src/`, `config/`, `selectors/`, shared test fixtures, unit/component tests, and the append-only `PROGRESS.md`. Existing flat `tests/test_*.py` files remain yours; place new focused suites in `tests/unit/` or `tests/component/` when practical. Codex ("sol") owns `tests/e2e/`, `review.md`, `WORKFLOW.md`, and `jobright-automation-spec.md` — you may read and run them, but never edit them.
- Unit/component tests should isolate individual functions, classes, modules, and bounded subsystem integrations. Codex writes cross-stage end-to-end and acceptance tests through public interfaces. If an end-to-end failure needs a production or focused-test change, use `PROGRESS.md` as the handoff rather than modifying `tests/e2e/`.
- Read `review.md` before changing code. If your work touches an open finding, resolve it with verification evidence or record a clear handoff in PROGRESS.md.
- Do not edit `jobright-automation-spec.md`. If implementation reveals a spec-relevant deviation, gap, or new idea, record it in PROGRESS.md — Codex folds it into the spec from there.
- You may spawn subagents to parallelize implementation and validation work within your lane.
- In automated rounds, never push or merge. Commit only to the assigned isolated branch; Codex independently reviews and owns integration.
- Follow the shared workflow and update the progress log after each step. Record proposed workflow changes in `PROGRESS.md`; Codex decides and applies any `WORKFLOW.md` correction.
- Keep task documentation short, factual, and evidence-based.
- If work is in progress, capture the current method, blocker, and mitigation path in PROGRESS.md.
- Do not claim a task or DoD is complete when its required command, live verification, or public entrypoint is missing.
