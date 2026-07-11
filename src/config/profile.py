"""profile.yaml schema and loader (spec T1.2 / S1.2.2).

Fields covered (verbatim from spec): canonical answers (work authorization,
sponsorship, notice period, salary expectation, address, phone, LinkedIn,
portfolio, EEO/self-ID preferences incl. "decline to answer" defaults),
plus free-text Q&A knowledge snippets.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

from src.contracts.exceptions import ConfigError

DeclinableAnswer = Literal["yes", "no", "decline_to_answer"]


class SalaryExpectation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    minimum: int | None = None
    maximum: int | None = None
    currency: str = "USD"


class Address(BaseModel):
    model_config = ConfigDict(extra="forbid")

    street: str = ""
    city: str = ""
    state: str = ""
    postal_code: str = ""
    country: str = ""


class EeoSelfId(BaseModel):
    """EEO / self-identification preferences. Defaults to declining, per
    spec's explicit call-out of 'decline to answer' defaults."""

    model_config = ConfigDict(extra="forbid")

    gender: str = "decline_to_answer"
    race_ethnicity: str = "decline_to_answer"
    veteran_status: DeclinableAnswer = "decline_to_answer"
    disability_status: DeclinableAnswer = "decline_to_answer"


class QASnippet(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str
    answer: str
    tags: list[str] = Field(default_factory=list)


class LearnedAnswer(BaseModel):
    """Appended to by Epic 7's answer-learning loop (T7.4). Empty at
    Epic 1 time; schema defined here so downstream epics have a stable
    shape to write into."""

    model_config = ConfigDict(extra="forbid")

    question: str
    answer: str
    ats: str | None = None
    job_id: str | None = None


class Profile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    full_name: str = ""
    email: str = ""
    phone: str = ""
    linkedin_url: str | None = None
    portfolio_url: str | None = None
    address: Address = Field(default_factory=Address)

    work_authorized: DeclinableAnswer = "decline_to_answer"
    requires_sponsorship: DeclinableAnswer = "decline_to_answer"
    notice_period_days: int | None = None
    salary_expectation: SalaryExpectation = Field(default_factory=SalaryExpectation)

    eeo_self_id: EeoSelfId = Field(default_factory=EeoSelfId)

    qa_snippets: list[QASnippet] = Field(default_factory=list)
    learned_answers: list[LearnedAnswer] = Field(default_factory=list)


def load_profile(path: str | Path) -> Profile:
    """Load and validate profile.yaml. Raises ConfigError with a clear
    message on any structural or validation failure (fail-fast)."""
    p = Path(path)
    if not p.exists():
        raise ConfigError(f"profile file not found: {p}")
    try:
        raw = yaml.safe_load(p.read_text()) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"profile file is not valid YAML: {p}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigError(f"profile file must contain a mapping at the top level: {p}")
    try:
        return Profile(**raw)
    except Exception as exc:
        raise ConfigError(f"profile file failed validation: {p}: {exc}") from exc
