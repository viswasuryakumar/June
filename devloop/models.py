from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Literal

Role = Literal["developer", "bug-fixer"]
TaskStatus = Literal["ready", "active", "completed", "blocked", "idle"]
SourceType = Literal["request", "review", "spec", "audit", "checkpoint"]


@dataclass(frozen=True)
class Task:
    task_id: str
    role: Role
    title: str
    prompt: str
    files: tuple[str, ...]
    acceptance_criteria: tuple[str, ...]
    verification_command: str
    estimated_minutes: int = 8
    dependencies: tuple[str, ...] = ()
    parent_task: str | None = None
    source_type: SourceType = "spec"
    source_id: str = "engineering-spec"
    completes_request: bool = False

    @classmethod
    def from_dict(cls, data: dict) -> Task:
        return cls(
            task_id=data["task_id"],
            role=data["role"],
            title=data["title"],
            prompt=data["prompt"],
            files=tuple(data.get("files", ())),
            acceptance_criteria=tuple(data.get("acceptance_criteria", ())),
            verification_command=data["verification_command"],
            estimated_minutes=int(data.get("estimated_minutes", 8)),
            dependencies=tuple(data.get("dependencies", ())),
            parent_task=data.get("parent_task"),
            source_type=data.get("source_type", "spec"),
            source_id=data.get("source_id", "engineering-spec"),
            completes_request=bool(data.get("completes_request", False)),
        )

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class WorkerResult:
    task_id: str
    role: Role
    status: Literal["completed", "blocked", "idle", "timed_out", "failed"]
    summary: str = ""
    changed_files: list[str] = field(default_factory=list)
    tests: list[str] = field(default_factory=list)
    commit_sha: str | None = None
    remaining_work: str = ""
    checkpoint_reason: str = ""
    branch: str = ""
    worktree: str = ""
    elapsed_seconds: float = 0.0

    @classmethod
    def from_dict(cls, data: dict) -> WorkerResult:
        return cls(**data)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ReviewResult:
    task_id: str
    approved: bool
    summary: str
    required_corrections: list[str] = field(default_factory=list)
    verification: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict) -> ReviewResult:
        return cls(**data)

    def to_dict(self) -> dict:
        return asdict(self)
