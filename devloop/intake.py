from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Literal

import yaml


class IntakeError(ValueError):
    pass


RequestStatus = Literal["proposed", "approved", "paused", "cancelled"]
Priority = Literal["critical", "high", "medium", "low"]
FeedbackVerdict = Literal["accepted", "changes-required"]
Lifecycle = Literal[
    "approved",
    "planned",
    "in-progress",
    "technical-approved",
    "delivered",
    "accepted",
    "blocked",
]

REQUEST_SECTIONS = (
    "Goal",
    "Why",
    "Required behavior",
    "Acceptance criteria",
    "Constraints and safety requirements",
    "Out of scope",
    "Examples or evidence",
)
FEEDBACK_SECTIONS = (
    "What worked",
    "What did not work",
    "Expected correction",
    "Evidence or run IDs",
)
PRIORITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}


@dataclass(frozen=True)
class UserRequest:
    request_id: str
    status: RequestStatus
    priority: Priority
    title: str
    spec_refs: tuple[str, ...]
    sections: dict[str, str]
    path: Path

    @property
    def acceptance_criteria(self) -> tuple[str, ...]:
        content = self.sections["Acceptance criteria"]
        items = [re.sub(r"^[-*]\s+", "", line).strip() for line in content.splitlines()]
        return tuple(item for item in items if item)

    def planner_summary(self, verdict: FeedbackVerdict | None = None) -> dict:
        return {
            "id": self.request_id,
            "status": self.status,
            "priority": self.priority,
            "title": self.title,
            "spec_refs": list(self.spec_refs),
            "goal": self.sections["Goal"],
            "acceptance_criteria": list(self.acceptance_criteria),
            "latest_feedback": verdict,
        }


@dataclass(frozen=True)
class UserFeedback:
    request_id: str
    verdict: FeedbackVerdict
    sections: dict[str, str]
    path: Path


@dataclass
class RequestState:
    request_id: str
    lifecycle: Lifecycle = "approved"
    child_task_ids: list[str] = field(default_factory=list)
    checkpoint_branches: list[str] = field(default_factory=list)
    integrated_commits: list[str] = field(default_factory=list)
    codex_reviews: list[dict] = field(default_factory=list)
    last_consumed_feedback: str | None = None
    unmet_acceptance_criteria: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class IntakeSnapshot:
    requests: dict[str, UserRequest]
    feedback: dict[str, tuple[UserFeedback, ...]]
    states: dict[str, RequestState]

    def latest_feedback(self, request_id: str) -> UserFeedback | None:
        items = self.feedback.get(request_id, ())
        return items[-1] if items else None

    def effective_lifecycle(self, request_id: str) -> Lifecycle:
        latest = self.latest_feedback(request_id)
        if latest and latest.verdict == "accepted":
            return "accepted"
        if latest and latest.verdict == "changes-required":
            return "in-progress"
        return self.states.get(request_id, RequestState(request_id)).lifecycle

    def eligible_requests(self) -> list[UserRequest]:
        eligible = []
        for request in self.requests.values():
            if request.status != "approved":
                continue
            latest = self.latest_feedback(request.request_id)
            if latest and latest.verdict == "accepted":
                continue
            eligible.append(request)
        return sorted(
            eligible,
            key=lambda item: (
                (
                    0
                    if (
                        self.latest_feedback(item.request_id)
                        and self.latest_feedback(item.request_id).verdict == "changes-required"
                    )
                    else 1
                ),
                PRIORITY_ORDER[item.priority],
                item.request_id,
            ),
        )

    def planner_context(self) -> str:
        return json.dumps(
            [
                request.planner_summary(
                    self.latest_feedback(request.request_id).verdict
                    if self.latest_feedback(request.request_id)
                    else None
                )
                for request in self.eligible_requests()
            ],
            indent=2,
            sort_keys=True,
        )


def _parse_document(path: Path, required_sections: tuple[str, ...]) -> tuple[dict, dict[str, str]]:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        raise IntakeError(f"{path}: missing YAML frontmatter")
    try:
        _, frontmatter, body = text.split("---", 2)
    except ValueError as exc:
        raise IntakeError(f"{path}: malformed YAML frontmatter") from exc
    metadata = yaml.safe_load(frontmatter) or {}
    if not isinstance(metadata, dict):
        raise IntakeError(f"{path}: frontmatter must be a mapping")
    sections: dict[str, str] = {}
    current: str | None = None
    lines: list[str] = []
    for line in body.splitlines():
        if line.startswith("## "):
            if current is not None:
                sections[current] = "\n".join(lines).strip()
            current = line[3:].strip()
            lines = []
        elif current is not None:
            lines.append(line)
    if current is not None:
        sections[current] = "\n".join(lines).strip()
    missing = [section for section in required_sections if not sections.get(section)]
    if missing:
        raise IntakeError(f"{path}: missing required sections: {', '.join(missing)}")
    return metadata, sections


def load_request(path: Path) -> UserRequest:
    metadata, sections = _parse_document(path, REQUEST_SECTIONS)
    required = {"id", "status", "priority", "title", "spec_refs"}
    missing = required - metadata.keys()
    if missing:
        raise IntakeError(f"{path}: missing fields: {', '.join(sorted(missing))}")
    extra = metadata.keys() - required
    if extra:
        raise IntakeError(f"{path}: unsupported user-intent fields: {', '.join(sorted(extra))}")
    request_id = str(metadata["id"])
    if not re.fullmatch(r"REQ-[0-9]{3,}", request_id):
        raise IntakeError(f"{path}: invalid request ID {request_id!r}")
    status = metadata["status"]
    priority = metadata["priority"]
    if status not in {"proposed", "approved", "paused", "cancelled"}:
        raise IntakeError(f"{path}: invalid request status {status!r}")
    if priority not in PRIORITY_ORDER:
        raise IntakeError(f"{path}: invalid priority {priority!r}")
    spec_refs = metadata["spec_refs"]
    if not isinstance(spec_refs, list):
        raise IntakeError(f"{path}: spec_refs must be a list")
    return UserRequest(
        request_id=request_id,
        status=status,
        priority=priority,
        title=str(metadata["title"]),
        spec_refs=tuple(str(item) for item in spec_refs),
        sections=sections,
        path=path,
    )


def load_feedback(path: Path) -> UserFeedback:
    metadata, sections = _parse_document(path, FEEDBACK_SECTIONS)
    if set(metadata) != {"request_id", "verdict"}:
        raise IntakeError(f"{path}: feedback fields must be exactly request_id and verdict")
    verdict = metadata["verdict"]
    if verdict not in {"accepted", "changes-required"}:
        raise IntakeError(f"{path}: invalid feedback verdict {verdict!r}")
    return UserFeedback(
        request_id=str(metadata["request_id"]), verdict=verdict, sections=sections, path=path
    )


def load_state(path: Path) -> RequestState:
    data = json.loads(path.read_text(encoding="utf-8"))
    return RequestState(**data)


def load_intake(repo: Path) -> IntakeSnapshot:
    user_root = repo / "coordination" / "user"
    requests: dict[str, UserRequest] = {}
    for path in sorted((user_root / "requests").glob("REQ-*.md")):
        request = load_request(path)
        if request.request_id in requests:
            raise IntakeError(f"duplicate request ID: {request.request_id}")
        requests[request.request_id] = request
    feedback: dict[str, list[UserFeedback]] = {}
    for path in sorted((user_root / "feedback").glob("*.md")):
        if path.name == "TEMPLATE.md":
            continue
        item = load_feedback(path)
        if item.request_id not in requests:
            raise IntakeError(f"{path}: feedback references unknown request {item.request_id}")
        feedback.setdefault(item.request_id, []).append(item)
    states = {
        path.stem: load_state(path)
        for path in sorted((repo / "coordination" / "state").glob("REQ-*.json"))
    }
    return IntakeSnapshot(
        requests=requests,
        feedback={key: tuple(value) for key, value in feedback.items()},
        states=states,
    )


def write_state(repo: Path, state: RequestState) -> Path:
    path = repo / "coordination" / "state" / f"{state.request_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state.to_dict(), indent=2, sort_keys=True), encoding="utf-8")
    return path
