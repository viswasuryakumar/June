# Multi-agent coordination

This directory holds current coordination state. It does not replace implementation history in `PROGRESS.md` or review evidence in `review.md`.

## Claiming work

Before editing, create `coordination/claims/<task-id>--<worker>.md` from `CLAIM_TEMPLATE.md`. Use a short stable task ID such as `e4-selection` or `rev-004`.

A claim is active when its `Status` is `active`. Two active claims may not name the same file or overlapping directory. If overlap is unavoidable, one worker must close or narrow its claim before the other starts.

Each concurrent writer works on a separate branch and worktree. Record both in the claim. Workers sharing the main checkout may inspect files, but only the designated sole writer may edit there.

At handoff, add verification and interface notes, change the status to `completed` or `blocked`, and update `WORKERS.md`. Do not delete claim files.

## Integration

The integration owner verifies the handoff, checks contract compatibility, and merges completed work. A claim is not evidence that its work passed; verification results are required.
