# CLAUDE.md

> This repository uses AGENTS.md as the canonical agent instructions file.
> Read AGENTS.md, WORKFLOW.md, and PROGRESS.md before doing any work in this repo.

Claude-specific notes:
- Your lane is `src/`, `config/`, `selectors/`, and `tests/`. Codex ("sol") owns `review.md` and `jobright-automation-spec.md` as a continuous review/orchestrator loop — read both, but never write to either.
- Read `review.md` before changing code. If your work touches an open finding, resolve it with verification evidence or record a clear handoff in PROGRESS.md.
- Do not edit `jobright-automation-spec.md`. If implementation reveals a spec-relevant deviation, gap, or new idea, record it in PROGRESS.md — Codex folds it into the spec from there.
- You may spawn subagents to parallelize implementation and validation work within your lane.
- Follow the shared workflow and update the progress log after each step; keep WORKFLOW.md current with any process changes.
- Keep task documentation short, factual, and evidence-based.
- If work is in progress, capture the current method, blocker, and mitigation path in PROGRESS.md.
- Do not claim a task or DoD is complete when its required command, live verification, or public entrypoint is missing.
