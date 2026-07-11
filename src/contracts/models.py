"""Shared pydantic data contracts (spec §3.1).

These models are the single source of truth for the shapes that flow
between every module/agent in the pipeline. Do not duplicate these
definitions elsewhere - import from here.

Epics 2-9 must treat these as frozen public contracts: extend via optional
fields with defaults, never break existing field names/types without a
version bump discussion in PROGRESS.md.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# --- Literal type aliases (spec §3.1 / §3.2) -------------------------------

RemoteType = Literal["remote", "hybrid", "onsite", "unknown"]
ApplyMode = Literal["agent", "extension", "manual_only", "unknown"]

# Spec §3.2 state machine:
# discovered -> selected -> resume_tailored -> applying ->
#   (needs_human <-> applying) -> submitted | failed | skipped
ApplicationStatus = Literal[
    "discovered",
    "selected",
    "resume_tailored",
    "applying",
    "needs_human",
    "submitted",
    "failed",
    "skipped",
]

HITLTicketKind = Literal[
    "captcha",
    "unknown_question",
    "final_approval",
    "login_2fa",
    "selector_broken",
]


class Job(BaseModel):
    """A single job listing as discovered from JobRight."""

    model_config = ConfigDict(extra="forbid")

    job_id: str = Field(..., description="JobRight internal id (from URL/DOM)")
    title: str
    company: str
    location: str
    remote_type: RemoteType = "unknown"
    salary_min: int | None = None
    salary_max: int | None = None
    match_score: int | None = Field(default=None, description="JobRight's score")
    posted_at: datetime | None = None
    jobright_url: str
    external_url: str | None = Field(default=None, description="the actual ATS/company posting")
    apply_mode: ApplyMode = "unknown"
    raw_description: str = ""


class ApplicationRecord(BaseModel):
    """Tracker row describing the lifecycle of a job application."""

    model_config = ConfigDict(extra="forbid")

    job_id: str
    status: ApplicationStatus = "discovered"
    resume_variant_path: str | None = None
    attempts: int = 0
    last_error: str | None = None
    screenshots: list[str] = Field(default_factory=list)
    timestamps: dict[str, datetime] = Field(default_factory=dict)


class HITLTicket(BaseModel):
    """A human-in-the-loop escalation ticket."""

    model_config = ConfigDict(extra="forbid")

    ticket_id: str
    job_id: str
    kind: HITLTicketKind
    context: dict = Field(
        default_factory=dict,
        description="screenshot path, question text, page URL, etc.",
    )
    resolution: str | None = None


ALL_MODELS: tuple[type[BaseModel], ...] = (Job, ApplicationRecord, HITLTicket)
