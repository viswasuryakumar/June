from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import signal
import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from devloop.contracts import SCHEMA_DIR, load_schema, unwrap_claude_json
from devloop.intake import IntakeError, IntakeSnapshot, RequestState, load_intake, write_state
from devloop.models import ReviewResult, Task, WorkerResult


class DevLoopError(RuntimeError):
    pass


@dataclass(frozen=True)
class DevLoopConfig:
    repo: Path
    interval_seconds: float = 900
    target_seconds: float = 480
    checkpoint_seconds: float = 540
    worker_timeout_seconds: float = 600
    integration_seconds: float = 300
    runtime_dir: Path | None = None
    worktree_root: Path = Path("/tmp/june-devloop")
    remote: str = "origin"
    main_branch: str = "main"
    push: bool = True

    def __post_init__(self) -> None:
        if not 0 < self.target_seconds <= self.checkpoint_seconds < self.worker_timeout_seconds:
            raise ValueError("require target <= checkpoint < worker timeout")
        if self.worker_timeout_seconds + self.integration_seconds > self.interval_seconds:
            raise ValueError("worker and integration budgets must fit inside one interval")

    @property
    def runs_dir(self) -> Path:
        return self.runtime_dir or self.repo / "runs" / "devloop"


CommandRunner = Callable[[Sequence[str], Path, float | None], subprocess.CompletedProcess[str]]
Planner = Callable[[str], list[Task]]
Reviewer = Callable[[Task, WorkerResult, Path, str], ReviewResult]


def default_command_runner(
    argv: Sequence[str], cwd: Path, timeout: float | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(argv), cwd=cwd, text=True, capture_output=True, timeout=timeout, check=False
    )


class DevLoopSupervisor:
    def __init__(
        self,
        config: DevLoopConfig,
        *,
        command_runner: CommandRunner = default_command_runner,
        planner: Planner | None = None,
        reviewer: Reviewer | None = None,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.config = config
        self.run_command = command_runner
        self.plan_provider = planner or self._plan_with_claude
        self.review_provider = reviewer or self._review_with_codex
        self.monotonic = monotonic
        self.sleep = sleep
        self._lock_path: Path | None = None

    def _git(self, *args: str, cwd: Path | None = None, timeout: float = 60) -> str:
        result = self.run_command(("git", *args), cwd or self.config.repo, timeout)
        if result.returncode != 0:
            raise DevLoopError(result.stderr.strip() or f"git {' '.join(args)} failed")
        return result.stdout.strip()

    def acquire_lock(self) -> None:
        self.config.runs_dir.mkdir(parents=True, exist_ok=True)
        lock_path = self.config.runs_dir / "supervisor.lock"
        for _attempt in range(2):
            try:
                lock_path.mkdir()
            except FileExistsError:
                if lock_path.is_file():
                    lock_path.unlink()
                    continue
                owner_path = lock_path / "owner.json"
                try:
                    owner = json.loads(owner_path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    owner = {}
                if self._process_matches(owner.get("pid"), owner.get("proc_start")):
                    raise DevLoopError("another dev-loop supervisor holds the lock") from None
                owner_path.unlink(missing_ok=True)
                try:
                    lock_path.rmdir()
                except OSError as exc:
                    raise DevLoopError("stale supervisor lock could not be recovered") from exc
                continue
            owner = {
                "pid": os.getpid(),
                "proc_start": self._process_start(os.getpid()),
                "acquired_at": datetime.now(UTC).isoformat(),
            }
            (lock_path / "owner.json").write_text(json.dumps(owner), encoding="utf-8")
            self._lock_path = lock_path
            return
        raise DevLoopError("could not acquire dev-loop supervisor lock")

    @staticmethod
    def _process_start(pid: int) -> str | None:
        try:
            return Path(f"/proc/{pid}/stat").read_text(encoding="utf-8").split()[21]
        except (OSError, IndexError):
            return None

    @classmethod
    def _process_matches(cls, pid: object, expected_start: object) -> bool:
        if not isinstance(pid, int) or expected_start is None:
            return False
        return cls._process_start(pid) == expected_start

    def release_lock(self) -> None:
        if self._lock_path is not None:
            (self._lock_path / "owner.json").unlink(missing_ok=True)
            self._lock_path.rmdir()
            self._lock_path = None

    def preflight(self, *, require_clean: bool = True) -> None:
        if require_clean and self._git("status", "--porcelain"):
            raise DevLoopError("main worktree is dirty; reconcile and commit it before live runs")
        branch = self._git("branch", "--show-current")
        if branch != self.config.main_branch:
            raise DevLoopError(
                f"expected {self.config.main_branch}, found {branch or 'detached HEAD'}"
            )
        if require_clean:
            self._git("fetch", self.config.remote, self.config.main_branch, timeout=120)
        self._git("rev-parse", "--verify", f"{self.config.remote}/{self.config.main_branch}")
        counts = self._git(
            "rev-list",
            "--left-right",
            "--count",
            f"{self.config.main_branch}...{self.config.remote}/{self.config.main_branch}",
        )
        ahead, behind = (int(value) for value in counts.split())
        if behind:
            raise DevLoopError(f"main is {behind} commit(s) behind the remote")
        if require_clean and ahead:
            raise DevLoopError(f"main has {ahead} unpushed commit(s)")
        if require_clean and self.config.push:
            push_check = self.run_command(
                ("git", "push", "--dry-run", self.config.remote, self.config.main_branch),
                self.config.repo,
                60,
            )
            if push_check.returncode != 0:
                raise DevLoopError(push_check.stderr.strip() or "remote push preflight failed")

    @staticmethod
    def validate_tasks(tasks: list[Task]) -> None:
        protected = (
            ".git",
            ".claude/agents",
            "AGENTS.md",
            "WORKFLOW.md",
            "review.md",
            "devloop",
            "coordination/history",
            "coordination/user",
            "coordination/state",
            "coordination/proposals",
        )
        roles = [task.role for task in tasks]
        if len(roles) != len(set(roles)):
            raise DevLoopError("planner returned more than one task for a role")
        for task in tasks:
            if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,63}", task.task_id):
                raise DevLoopError(f"unsafe task ID: {task.task_id!r}")
            if task.estimated_minutes > 8:
                raise DevLoopError(f"{task.task_id} exceeds the eight-minute planning limit")
            if not task.files:
                raise DevLoopError(f"{task.task_id} has no file claim")
            for claimed in task.files:
                path = Path(claimed)
                if path.is_absolute() or ".." in path.parts:
                    raise DevLoopError(f"{task.task_id} has unsafe file claim: {claimed}")
                if any(
                    claimed == item or claimed.startswith(f"{item.rstrip('/')}/")
                    for item in protected
                ):
                    raise DevLoopError(
                        f"{task.task_id} claims protected control-plane file: {claimed}"
                    )
        if len(tasks) == 2 and DevLoopSupervisor._claims_overlap(tasks[0].files, tasks[1].files):
            raise DevLoopError("developer and bug-fixer claims overlap")

    @staticmethod
    def validate_task_sources(tasks: list[Task], intake: IntakeSnapshot) -> None:
        eligible = intake.eligible_requests()
        eligible_ids = {request.request_id for request in eligible}
        top_request = eligible[0].request_id if eligible else None
        for task in tasks:
            if task.source_type == "request":
                if task.source_id not in eligible_ids:
                    raise DevLoopError(
                        f"{task.task_id} references ineligible request {task.source_id}"
                    )
                if task.source_id != top_request:
                    raise DevLoopError(
                        f"{task.task_id} skipped higher-priority eligible request {top_request}"
                    )
            elif task.completes_request:
                raise DevLoopError(
                    f"{task.task_id} can complete a request only when source_type is request"
                )

    @staticmethod
    def _claims_overlap(left: tuple[str, ...], right: tuple[str, ...]) -> bool:
        def overlaps(a: str, b: str) -> bool:
            a = a.rstrip("/")
            b = b.rstrip("/")
            return a == b or a.startswith(f"{b}/") or b.startswith(f"{a}/")

        return any(overlaps(a, b) for a in left for b in right)

    def _plan_with_claude(self, round_id: str) -> list[Task]:
        try:
            intake = load_intake(self.config.repo)
        except IntakeError as exc:
            raise DevLoopError(f"invalid user intake: {exc}") from exc
        intake_context = intake.planner_context()
        prompt = (
            f"Plan round {round_id}. Read AGENTS.md, WORKFLOW.md, coordination/WORKERS.md, "
            "PROGRESS.md, review.md, coordination/user/DECISIONS.md, and the specification. "
            f"Eligible approved requests in deterministic priority order:\n{intake_context}\n"
            "Return at most one developer task "
            "and one bug-fixer task. Each must be independently deliverable in eight minutes, "
            "claim exact non-overlapping files, and include one bounded verification command. "
            "Priority is: changes-required feedback, approved critical/high requests, checkpoints, "
            "critical/high review findings, approved medium/low requests, then remaining spec work. "
            "A request task must reference the first eligible request above. Set completes_request "
            "true only when the task satisfies every remaining acceptance criterion. If a request "
            "conflicts with the technical safety baseline, return no task until the user records a "
            "decision. Otherwise give Bug-fixer one bounded audit when no defect is ready. Return no "
            "task for a role when no safe task exists. Do not edit files."
        )
        schema = json.dumps(load_schema("planner"), separators=(",", ":"))
        result = self.run_command(
            (
                "claude",
                "--print",
                "--agent",
                "coordinator",
                "--permission-mode",
                "plan",
                "--output-format",
                "json",
                "--json-schema",
                schema,
                prompt,
            ),
            self.config.repo,
            min(240.0, self.config.target_seconds),
        )
        if result.returncode != 0:
            raise DevLoopError(result.stderr.strip() or "Claude coordinator failed")
        data = unwrap_claude_json(result.stdout)
        tasks = [Task.from_dict(item) for item in data.get("tasks", [])]
        self.validate_tasks(tasks)
        self.validate_task_sources(tasks, intake)
        return tasks

    def _create_worktree(self, task: Task, round_id: str) -> tuple[str, Path]:
        branch = f"devloop/{round_id}/{task.role}/{task.task_id}"
        worktree = self.config.worktree_root / round_id / task.role
        worktree.parent.mkdir(parents=True, exist_ok=True)
        self._git("worktree", "add", "-b", branch, str(worktree), self.config.main_branch)
        return branch, worktree

    def _worker_prompt(self, task: Task, round_id: str) -> str:
        criteria = "\n".join(f"- {item}" for item in task.acceptance_criteria)
        files = "\n".join(f"- {item}" for item in task.files)
        return f"""Round: {round_id}
Task: {task.task_id} — {task.title}
Role: {task.role}
Source: {task.source_type}:{task.source_id}
Completes source request: {task.completes_request}
Target: finish within 8 minutes. At 9 minutes checkpoint. Never exceed 10 minutes.

Instructions:
{task.prompt}

Claimed files:
{files}

Acceptance criteria:
{criteria}

Verification command: {task.verification_command}

Stay inside the claim. Run verification, update your lane history when applicable, commit completed
work to this branch using the task ID, and return the required JSON result. Never push or merge.
If the task cannot finish, do not claim completion; describe the smallest continuation and leave a
recoverable checkpoint on this branch.
"""

    def _run_worker(self, task: Task, round_id: str, branch: str, worktree: Path) -> WorkerResult:
        started = self.monotonic()
        schema = json.dumps(load_schema("worker_result"), separators=(",", ":"))
        argv = (
            "claude",
            "--print",
            "--agent",
            task.role,
            "--permission-mode",
            "acceptEdits",
            "--disallowed-tools",
            "Bash(git push *),Bash(git merge *),Bash(git cherry-pick *),Bash(git checkout main)",
            "--output-format",
            "json",
            "--json-schema",
            schema,
            self._worker_prompt(task, round_id),
        )
        process = subprocess.Popen(
            argv,
            cwd=worktree,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
            close_fds=True,
        )
        checkpoint_path = self.config.runs_dir / round_id / f"{task.role}-checkpoint.json"
        try:
            stdout, stderr = process.communicate(timeout=self.config.checkpoint_seconds)
        except subprocess.TimeoutExpired:
            checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
            checkpoint_path.write_text(
                json.dumps(
                    {
                        "task_id": task.task_id,
                        "requested_at_seconds": self.config.checkpoint_seconds,
                    }
                ),
                encoding="utf-8",
            )
            remaining = self.config.worker_timeout_seconds - self.config.checkpoint_seconds
            try:
                stdout, stderr = process.communicate(timeout=remaining)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    stdout, stderr = process.communicate(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    stdout, stderr = process.communicate()
                return WorkerResult(
                    task_id=task.task_id,
                    role=task.role,
                    status="timed_out",
                    summary=stderr.strip(),
                    remaining_work="Resume from the preserved checkpoint branch in a smaller task.",
                    checkpoint_reason="hard ten-minute deadline reached",
                    branch=branch,
                    worktree=str(worktree),
                    elapsed_seconds=self.monotonic() - started,
                )
        if process.returncode != 0:
            return WorkerResult(
                task_id=task.task_id,
                role=task.role,
                status="failed",
                summary=stderr.strip() or "worker process failed",
                branch=branch,
                worktree=str(worktree),
                elapsed_seconds=self.monotonic() - started,
            )
        data = unwrap_claude_json(stdout)
        result = WorkerResult.from_dict(data)
        if result.task_id != task.task_id or result.role != task.role:
            return WorkerResult(
                task_id=task.task_id,
                role=task.role,
                status="failed",
                summary="worker result identity did not match the assigned task",
                branch=branch,
                worktree=str(worktree),
                elapsed_seconds=self.monotonic() - started,
            )
        result.branch = branch
        result.worktree = str(worktree)
        result.elapsed_seconds = self.monotonic() - started
        return result

    def _changed_files(self, worktree: Path) -> list[str]:
        output = self._git("diff", "--name-only", f"{self.config.main_branch}...HEAD", cwd=worktree)
        return [line for line in output.splitlines() if line]

    @staticmethod
    def _inside_claim(path: str, claims: tuple[str, ...]) -> bool:
        return any(
            path == claim.rstrip("/") or path.startswith(f"{claim.rstrip('/')}/")
            for claim in claims
        )

    def validate_worker_result(self, task: Task, result: WorkerResult, worktree: Path) -> None:
        if result.status != "completed":
            return
        changed = self._changed_files(worktree)
        outside = [path for path in changed if not self._inside_claim(path, task.files)]
        if outside:
            raise DevLoopError(f"{task.task_id} changed files outside its claim: {outside}")
        if not changed:
            raise DevLoopError(f"{task.task_id} completed without a change")
        if self._git("status", "--porcelain", cwd=worktree):
            raise DevLoopError(f"{task.task_id} left uncommitted work")
        result.commit_sha = self._git("rev-parse", "HEAD", cwd=worktree)
        result.changed_files = changed

    def _review_with_codex(
        self, task: Task, result: WorkerResult, worktree: Path, round_id: str
    ) -> ReviewResult:
        output = self.config.runs_dir / round_id / f"codex-{task.task_id}.json"
        output.parent.mkdir(parents=True, exist_ok=True)
        request_acceptance: list[str] = []
        if task.source_type == "request":
            try:
                request = load_intake(self.config.repo).requests.get(task.source_id)
            except IntakeError as exc:
                return ReviewResult(
                    task_id=task.task_id,
                    approved=False,
                    summary=f"Invalid user intake during review: {exc}",
                    required_corrections=["Repair intake before retrying review."],
                )
            if request:
                request_acceptance = list(request.acceptance_criteria)
        prompt = (
            f"Independently review task {task.task_id} against {self.config.main_branch}. "
            f"Source: {task.source_type}:{task.source_id}. "
            f"Claimed files: {list(task.files)}. Acceptance: {list(task.acceptance_criteria)}. "
            f"Full source-request acceptance: {request_acceptance}. "
            f"Worker claims this completes the request: {task.completes_request}. "
            f"Required verification: {task.verification_command}. Inspect only; do not edit, commit, "
            "merge, or push. Approve only if scope, behavior, regression coverage, and evidence are sound."
        )
        result_cmd = self.run_command(
            (
                "codex",
                "exec",
                "--ephemeral",
                "-s",
                "read-only",
                "-a",
                "never",
                "-C",
                str(worktree),
                "--output-schema",
                str(SCHEMA_DIR / "review_result.json"),
                "-o",
                str(output),
                prompt,
            ),
            worktree,
            self.config.integration_seconds,
        )
        if result_cmd.returncode != 0 or not output.exists():
            return ReviewResult(
                task_id=task.task_id,
                approved=False,
                summary=result_cmd.stderr.strip() or "Codex review failed",
                required_corrections=["Retry independent review in the next round."],
            )
        return ReviewResult.from_dict(json.loads(output.read_text(encoding="utf-8")))

    def _verification_argv(self, command: str) -> list[str]:
        argv = shlex.split(command)
        forbidden = {";", "&&", "||", "|", ">", ">>", "<"}
        if not argv or forbidden.intersection(argv):
            raise DevLoopError(
                "verification command must be a single command without shell operators"
            )
        allowed = (
            argv[:3] == ["git", "diff", "--check"]
            or argv[0] in {"pytest", ".venv/bin/pytest"}
            or (
                argv[0] in {"python", "python3", ".venv/bin/python"}
                and argv[1:3] == ["-m", "pytest"]
            )
            or (argv[0] in {"ruff", ".venv/bin/ruff"} and len(argv) > 1 and argv[1] == "check")
            or (argv[0] in {"black", ".venv/bin/black"} and "--check" in argv[1:])
        )
        if not allowed:
            raise DevLoopError(f"verification executable is not allowlisted: {argv[0]}")
        return argv

    def _cleanup_worktree(self, worktree: Path, branch: str) -> None:
        self._git("worktree", "remove", "--force", str(worktree))
        self._git("branch", "-D", branch)

    def integrate(
        self,
        task: Task,
        result: WorkerResult,
        review: ReviewResult | None = None,
        round_id: str = "",
    ) -> str:
        if not result.commit_sha:
            raise DevLoopError(f"{task.task_id} has no commit to integrate")
        self._git("cherry-pick", "--no-commit", result.commit_sha)
        verify = self.run_command(
            self._verification_argv(task.verification_command),
            self.config.repo,
            self.config.integration_seconds,
        )
        if verify.returncode != 0:
            self._git("cherry-pick", "--abort")
            raise DevLoopError(verify.stderr.strip() or f"verification failed for {task.task_id}")
        history = self.config.repo / "coordination" / "history"
        history.mkdir(parents=True, exist_ok=True)
        evidence_path = history / f"{task.task_id}.json"
        evidence_path.write_text(
            json.dumps(
                {
                    "round_id": round_id,
                    "task": task.to_dict(),
                    "worker_result": result.to_dict(),
                    "review": review.to_dict() if review else None,
                    "integrated_at": datetime.now(UTC).isoformat(),
                },
                indent=2,
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        self._git("add", str(evidence_path.relative_to(self.config.repo)))
        if task.source_type == "request":
            try:
                snapshot = load_intake(self.config.repo)
            except IntakeError as exc:
                self._git("cherry-pick", "--abort")
                raise DevLoopError(f"invalid user intake during integration: {exc}") from exc
            state = snapshot.states.get(task.source_id, RequestState(task.source_id))
            if task.task_id not in state.child_task_ids:
                state.child_task_ids.append(task.task_id)
            if review:
                state.codex_reviews.append(review.to_dict())
            latest = snapshot.latest_feedback(task.source_id)
            if latest and latest.verdict == "changes-required":
                state.last_consumed_feedback = str(latest.path.relative_to(self.config.repo))
            state.lifecycle = "delivered" if task.completes_request else "in-progress"
            state.unmet_acceptance_criteria = (
                [] if task.completes_request else list(task.acceptance_criteria)
            )
            state_path = write_state(self.config.repo, state)
            self._git("add", str(state_path.relative_to(self.config.repo)))
        self._git("commit", "-m", f"{task.task_id}: {task.title}")
        integrated_sha = self._git("rev-parse", "HEAD")
        if self.config.push:
            self._git("push", self.config.remote, self.config.main_branch, timeout=120)
        return integrated_sha

    def _write_round(self, round_id: str, payload: dict) -> None:
        path = self.config.runs_dir / round_id / "round.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")

    def _maybe_write_improvement_proposal(self) -> Path | None:
        marker = self.config.runs_dir / "last-improvement-round.txt"
        last_round = marker.read_text(encoding="utf-8").strip() if marker.exists() else ""
        all_rounds = sorted(self.config.runs_dir.glob("*/round.json"))
        rounds = [path for path in all_rounds if path.parent.name > last_round][-8:]
        if not rounds:
            return None
        payloads = [json.loads(path.read_text(encoding="utf-8")) for path in rounds]
        results = [item for payload in payloads for item in payload.get("results", [])]
        failures = sum(item.get("status") in {"timed_out", "failed", "blocked"} for item in results)
        completed = [item for item in results if item.get("status") == "completed"]
        average_seconds = (
            round(
                sum(float(item.get("elapsed_seconds", 0)) for item in completed) / len(completed), 2
            )
            if completed
            else 0
        )
        reviews = [item for payload in payloads for item in payload.get("reviews", [])]
        rejections = sum(not item.get("approved", False) for item in reviews)
        try:
            intake = load_intake(self.config.repo)
            changes_required = sum(
                bool(intake.latest_feedback(request_id))
                and intake.latest_feedback(request_id).verdict == "changes-required"
                for request_id in intake.requests
            )
        except IntakeError:
            changes_required = 0
        existing = list((self.config.repo / "coordination" / "proposals").glob("IMP-*.md"))
        if len(rounds) < 8 and failures + rejections < 3:
            return None
        proposal_id = f"IMP-{len(existing) + 1:03d}"
        path = self.config.repo / "coordination" / "proposals" / f"{proposal_id}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            f"""---
id: {proposal_id}
status: proposed
rounds_observed: {len(rounds)}
---

# {proposal_id} — Development-loop retrospective

## Evidence

- Worker failures, blocks, or timeouts: {failures}
- Codex rejections: {rejections}
- Completed worker results: {len(completed)}
- Average completed-worker seconds: {average_seconds}
- Requests currently carrying changes-required feedback: {changes_required}
- Round files reviewed: {len(rounds)}

## Observed problem

Review the failed or rejected round evidence for repeated task-sizing, prompt, verification, or workflow issues.

## Proposed improvement

Produce one bounded control-plane change only after the user records an approved proposal decision.

## Expected improvement

Lower the observed failure or rejection rate without weakening scope, review, or verification gates.

## Risks

Prompt or timing changes can reduce safety or hide incomplete work.

## Validation experiment

Run the full dev-loop regression suite and one supervised dry-run, then compare the next eight rounds.

## Rollback rule

Revert if failures or rejections increase, scope enforcement weakens, or integration evidence is lost.
""",
            encoding="utf-8",
        )
        marker.write_text(rounds[-1].parent.name, encoding="utf-8")
        return path

    def run_once(self, *, dry_run: bool = False, _manage_lock: bool = True) -> dict:
        if _manage_lock:
            self.acquire_lock()
        round_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        started = self.monotonic()
        try:
            self.preflight(require_clean=not dry_run)
            tasks = self.plan_provider(round_id)
            self.validate_tasks(tasks)
            try:
                intake = load_intake(self.config.repo)
            except IntakeError as exc:
                raise DevLoopError(f"invalid user intake: {exc}") from exc
            self.validate_task_sources(tasks, intake)
            if dry_run:
                payload = {
                    "round_id": round_id,
                    "dry_run": True,
                    "tasks": [task.to_dict() for task in tasks],
                }
                self._write_round(round_id, payload)
                return payload
            work = []
            for task in tasks:
                branch, worktree = self._create_worktree(task, round_id)
                work.append((task, branch, worktree))
            with ThreadPoolExecutor(max_workers=2) as pool:
                futures = {
                    pool.submit(self._run_worker, task, round_id, branch, worktree): (
                        task,
                        worktree,
                    )
                    for task, branch, worktree in work
                }
                results = [
                    (task, worktree, future.result())
                    for future, (task, worktree) in futures.items()
                ]
            integrations = []
            reviews = []
            for task, worktree, result in results:
                try:
                    self.validate_worker_result(task, result, worktree)
                except DevLoopError as exc:
                    result.status = "failed"
                    result.summary = str(exc)
                if result.status != "completed":
                    reviews.append(ReviewResult(task.task_id, False, result.summary).to_dict())
                    continue
                review = self.review_provider(task, result, worktree, round_id)
                reviews.append(review.to_dict())
                if review.approved:
                    integrated_sha = self.integrate(task, result, review, round_id)
                    integrations.append(
                        {
                            "task_id": task.task_id,
                            "commit_sha": integrated_sha,
                        }
                    )
                    self._cleanup_worktree(worktree, result.branch)
            payload = {
                "round_id": round_id,
                "dry_run": False,
                "tasks": [task.to_dict() for task in tasks],
                "results": [result.to_dict() for _, _, result in results],
                "reviews": reviews,
                "integrations": integrations,
                "elapsed_seconds": self.monotonic() - started,
            }
            self._write_round(round_id, payload)
            proposal = self._maybe_write_improvement_proposal()
            if proposal is not None:
                self._git("add", str(proposal.relative_to(self.config.repo)))
                self._git("commit", "-m", f"docs(devloop): add {proposal.stem} retrospective")
                if self.config.push:
                    self._git("push", self.config.remote, self.config.main_branch, timeout=120)
            return payload
        finally:
            if _manage_lock:
                self.release_lock()

    def run_forever(self, *, max_rounds: int | None = None) -> None:
        self.acquire_lock()
        try:
            next_tick = self.monotonic()
            rounds = 0
            while max_rounds is None or rounds < max_rounds:
                wait = next_tick - self.monotonic()
                if wait > 0:
                    self.sleep(wait)
                try:
                    self.run_once(_manage_lock=False)
                except DevLoopError as exc:
                    print(f"dev-loop round blocked: {exc}", file=sys.stderr)
                rounds += 1
                next_tick += self.config.interval_seconds
                now = self.monotonic()
                while next_tick <= now:
                    next_tick += self.config.interval_seconds
        finally:
            self.release_lock()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m devloop")
    parser.add_argument("mode", choices=("run", "once", "dry-run"))
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--no-push", action="store_true")
    parser.add_argument("--interval-seconds", type=float, default=900)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = DevLoopConfig(
        repo=args.repo.resolve(), interval_seconds=args.interval_seconds, push=not args.no_push
    )
    supervisor = DevLoopSupervisor(config)
    try:
        if args.mode == "run":
            if os.environ.get("JUNE_DEVLOOP_ENABLE") != "1":
                raise DevLoopError("set JUNE_DEVLOOP_ENABLE=1 to enable continuous live execution")
            supervisor.run_forever()
            return 0
        payload = supervisor.run_once(dry_run=args.mode == "dry-run")
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0
    except DevLoopError as exc:
        print(f"dev-loop blocked: {exc}", file=sys.stderr)
        return 2
