from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from devloop.models import Task, WorkerResult
from devloop.supervisor import DevLoopConfig, DevLoopError, DevLoopSupervisor


def _run(*argv: str, cwd: Path) -> None:
    subprocess.run(argv, cwd=cwd, check=True, text=True, capture_output=True)


def initialized_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    remote = tmp_path / "remote.git"
    repo.mkdir()
    _run("git", "init", cwd=repo)
    _run("git", "checkout", "-b", "main", cwd=repo)
    _run("git", "config", "user.name", "Dev Loop Test", cwd=repo)
    _run("git", "config", "user.email", "devloop@example.invalid", cwd=repo)
    (repo / "README.md").write_text("baseline\n", encoding="utf-8")
    _run("git", "add", "README.md", cwd=repo)
    _run("git", "commit", "-m", "baseline", cwd=repo)
    _run("git", "init", "--bare", str(remote), cwd=tmp_path)
    _run("git", "remote", "add", "origin", str(remote), cwd=repo)
    _run("git", "push", "-u", "origin", "main", cwd=repo)
    return repo


def task(role: str, task_id: str, files: tuple[str, ...]) -> Task:
    return Task(
        task_id=task_id,
        role=role,  # type: ignore[arg-type]
        title=f"{role} task",
        prompt="Make one bounded change.",
        files=files,
        acceptance_criteria=("Focused behavior is verified.",),
        verification_command="git diff --check",
    )


def config(repo: Path, **overrides) -> DevLoopConfig:
    values = {
        "repo": repo,
        "runtime_dir": repo / "runs" / "devloop-test",
        "worktree_root": repo.parent / "worktrees",
        "push": False,
    }
    values.update(overrides)
    return DevLoopConfig(**values)


def test_config_enforces_worker_and_integration_budget(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="fit inside one interval"):
        config(
            tmp_path,
            interval_seconds=100,
            target_seconds=20,
            checkpoint_seconds=30,
            worker_timeout_seconds=60,
            integration_seconds=60,
        )


def test_planner_rejects_long_or_overlapping_tasks() -> None:
    long_task = task("developer", "too-long", ("src/a.py",))
    object.__setattr__(long_task, "estimated_minutes", 9)
    with pytest.raises(DevLoopError, match="eight-minute"):
        DevLoopSupervisor.validate_tasks([long_task])

    with pytest.raises(DevLoopError, match="overlap"):
        DevLoopSupervisor.validate_tasks(
            [
                task("developer", "feature", ("src/shared",)),
                task("bug-fixer", "bug", ("src/shared/file.py",)),
            ]
        )


def test_planner_rejects_unsafe_ids_and_control_plane_claims() -> None:
    with pytest.raises(DevLoopError, match="unsafe task ID"):
        DevLoopSupervisor.validate_tasks([task("developer", "../escape", ("src/a.py",))])
    with pytest.raises(DevLoopError, match="protected control-plane"):
        DevLoopSupervisor.validate_tasks([task("bug-fixer", "rewrite-loop", ("devloop",))])
    with pytest.raises(DevLoopError, match="protected control-plane"):
        DevLoopSupervisor.validate_tasks(
            [task("developer", "rewrite-user", ("coordination/user/requests/REQ-005.md",))]
        )


def test_verification_command_is_allowlisted(tmp_path: Path) -> None:
    supervisor = DevLoopSupervisor(config(tmp_path), planner=lambda _round_id: [])
    assert supervisor._verification_argv(".venv/bin/python -m pytest -q tests/unit") == [
        ".venv/bin/python",
        "-m",
        "pytest",
        "-q",
        "tests/unit",
    ]
    with pytest.raises(DevLoopError, match="not allowlisted"):
        supervisor._verification_argv("rm -rf runs")


def test_dry_run_plans_without_requiring_clean_tree(tmp_path: Path) -> None:
    repo = initialized_repo(tmp_path)
    (repo / "local-note.txt").write_text("uncommitted\n", encoding="utf-8")
    planned = [
        task("developer", "e5-contract", ("src/resume/contracts.py",)),
        task("bug-fixer", "rev-007", ("tests/test_tracker_repository.py",)),
    ]
    supervisor = DevLoopSupervisor(config(repo), planner=lambda _round_id: planned)

    result = supervisor.run_once(dry_run=True)

    assert result["dry_run"] is True
    assert [item["task_id"] for item in result["tasks"]] == ["e5-contract", "rev-007"]
    round_file = next((repo / "runs" / "devloop-test").glob("*/round.json"))
    assert json.loads(round_file.read_text(encoding="utf-8"))["dry_run"] is True


def test_live_preflight_rejects_dirty_main(tmp_path: Path) -> None:
    repo = initialized_repo(tmp_path)
    (repo / "README.md").write_text("dirty\n", encoding="utf-8")
    supervisor = DevLoopSupervisor(config(repo), planner=lambda _round_id: [])

    with pytest.raises(DevLoopError, match="dirty"):
        supervisor.run_once()


def test_second_supervisor_cannot_take_active_lock(tmp_path: Path) -> None:
    repo = initialized_repo(tmp_path)
    first = DevLoopSupervisor(config(repo), planner=lambda _round_id: [])
    second = DevLoopSupervisor(config(repo), planner=lambda _round_id: [])
    first.acquire_lock()
    try:
        with pytest.raises(DevLoopError, match="holds the lock"):
            second.acquire_lock()
    finally:
        first.release_lock()


def test_stale_supervisor_lock_is_recovered(tmp_path: Path) -> None:
    repo = initialized_repo(tmp_path)
    supervisor = DevLoopSupervisor(config(repo), planner=lambda _round_id: [])
    lock = repo / "runs" / "devloop-test" / "supervisor.lock"
    lock.mkdir(parents=True)
    (lock / "owner.json").write_text(
        json.dumps({"pid": 999999, "proc_start": "not-running"}), encoding="utf-8"
    )

    supervisor.acquire_lock()
    try:
        owner = json.loads((lock / "owner.json").read_text(encoding="utf-8"))
        assert owner["pid"] != 999999
    finally:
        supervisor.release_lock()


def test_completed_result_must_stay_in_claim_and_be_committed(tmp_path: Path) -> None:
    repo = initialized_repo(tmp_path)
    supervisor = DevLoopSupervisor(config(repo), planner=lambda _round_id: [])
    feature = task("developer", "bounded", ("allowed",))
    branch = "devloop/test/developer/bounded"
    worktree = repo.parent / "bounded-worktree"
    _run("git", "worktree", "add", "-b", branch, str(worktree), "main", cwd=repo)
    (worktree / "outside.txt").write_text("outside\n", encoding="utf-8")
    _run("git", "add", "outside.txt", cwd=worktree)
    _run("git", "commit", "-m", "outside claim", cwd=worktree)

    result = WorkerResult(task_id="bounded", role="developer", status="completed")
    with pytest.raises(DevLoopError, match="outside its claim"):
        supervisor.validate_worker_result(feature, result, worktree)


def test_timeout_preserves_checkpoint_branch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = initialized_repo(tmp_path)
    supervisor = DevLoopSupervisor(
        config(
            repo,
            interval_seconds=0.2,
            target_seconds=0.01,
            checkpoint_seconds=0.02,
            worker_timeout_seconds=0.05,
            integration_seconds=0.1,
        ),
        planner=lambda _round_id: [],
    )

    class NeverFinishes:
        pid = 999999
        returncode = None

        def __init__(self):
            self.calls = 0

        def communicate(self, timeout=None):
            self.calls += 1
            if self.calls <= 2:
                raise subprocess.TimeoutExpired("claude", timeout)
            return "", "terminated at deadline"

    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: NeverFinishes())
    monkeypatch.setattr("devloop.supervisor.os.killpg", lambda *args: None)
    times = iter((0.0, 0.05))
    supervisor.monotonic = lambda: next(times)
    bounded = task("developer", "checkpoint", ("src/example.py",))

    result = supervisor._run_worker(bounded, "round-1", "branch-1", repo)

    assert result.status == "timed_out"
    assert result.branch == "branch-1"
    assert result.checkpoint_reason == "hard ten-minute deadline reached"
    checkpoint = repo / "runs" / "devloop-test" / "round-1" / "developer-checkpoint.json"
    assert checkpoint.exists()


def test_fixed_cadence_waits_after_early_completion(tmp_path: Path) -> None:
    repo = initialized_repo(tmp_path)
    clock = {"now": 0.0}
    sleeps: list[float] = []

    class FakeSupervisor(DevLoopSupervisor):
        def run_once(self, *, dry_run: bool = False, _manage_lock: bool = True) -> dict:
            clock["now"] += 100
            return {}

    def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)
        clock["now"] += seconds

    supervisor = FakeSupervisor(config(repo), monotonic=lambda: clock["now"], sleep=fake_sleep)
    supervisor.run_forever(max_rounds=2)

    assert sleeps == [800]
    assert clock["now"] == 1000


def test_integrator_applies_verified_commit_without_push(tmp_path: Path) -> None:
    repo = initialized_repo(tmp_path)
    supervisor = DevLoopSupervisor(config(repo), planner=lambda _round_id: [])
    bounded = task("developer", "bounded-change", ("allowed",))
    branch = "devloop/test/developer/bounded-change"
    worktree = repo.parent / "integration-worktree"
    _run("git", "worktree", "add", "-b", branch, str(worktree), "main", cwd=repo)
    (worktree / "allowed").mkdir()
    (worktree / "allowed" / "feature.txt").write_text("verified\n", encoding="utf-8")
    _run("git", "add", "allowed/feature.txt", cwd=worktree)
    _run("git", "commit", "-m", "bounded branch commit", cwd=worktree)
    result = WorkerResult(
        task_id=bounded.task_id,
        role="developer",
        status="completed",
        commit_sha=subprocess.run(
            ("git", "rev-parse", "HEAD"),
            cwd=worktree,
            check=True,
            text=True,
            capture_output=True,
        ).stdout.strip(),
    )

    integrated_sha = supervisor.integrate(bounded, result)

    assert (repo / "allowed" / "feature.txt").read_text(encoding="utf-8") == "verified\n"
    assert integrated_sha != result.commit_sha
    evidence = repo / "coordination" / "history" / "bounded-change.json"
    assert json.loads(evidence.read_text(encoding="utf-8"))["task"]["task_id"] == "bounded-change"
    assert (
        subprocess.run(
            ("git", "status", "--porcelain"), cwd=repo, check=True, text=True, capture_output=True
        ).stdout
        == ""
    )
