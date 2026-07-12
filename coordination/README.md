# Multi-agent coordination

This directory holds current coordination state. It does not replace implementation history in `PROGRESS.md` or review evidence in `review.md`.

`coordination/user/` is the human-owned product inbox. Requests determine priority; feedback accepts
or reopens delivered behavior; decisions resolve ambiguity; proposal decisions gate changes to the
development workflow. Agents never edit those inputs.

## Claiming work

Before editing, create `coordination/claims/<task-id>--<worker>.md` from `CLAIM_TEMPLATE.md`. Use a short stable task ID such as `e4-selection` or `rev-004`.

A claim is active when its `Status` is `active`. Two active claims may not name the same file or overlapping directory. If overlap is unavoidable, one worker must close or narrow its claim before the other starts.

Each concurrent writer works on a separate branch and worktree. Record both in the claim. Workers sharing the main checkout may inspect files, but only the designated sole writer may edit there.

At handoff, add verification and interface notes, change the status to `completed` or `blocked`, and update `WORKERS.md`. Do not delete claim files.

## Automated rounds

`make dev-loop` runs the four-agent supervisor on fixed 15-minute ticks. Claude Coordinator assigns
one non-overlapping task each to Developer and Bug-fixer. Their target is eight minutes, checkpoint
is requested at nine, and the hard deadline is ten. Codex independently reviews completed branches;
only approved work is integrated and pushed. Early completion ends the worker process, while the
supervisor waits for the next tick. Empty rounds are recorded under ignored `runs/devloop/` without
empty commits.

Use `make dev-loop-dry-run` to inspect proposed tasks. Continuous execution is deliberately gated by
`JUNE_DEVLOOP_ENABLE=1` and refuses a dirty, behind, or unpushed `main` branch.

Only approved requests are eligible. The supervisor parses and validates request briefs before
planning, records source traceability on every task, stores delivery state in `coordination/state/`,
and keeps original user text unchanged.

## Integration

Codex is the integration owner. It verifies the handoff, checks contract compatibility, reruns the
bounded verification command, merges approved commits sequentially, and pushes `main`. A claim is not
evidence that its work passed; verification results are required. Codex rejects rather than silently
rewrites implementation work.
