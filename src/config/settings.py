"""settings.yaml schema and loader (spec T1.2 / S1.2.1).

Fields covered (verbatim from spec):
max_applications_per_day, min_match_score, title include/exclude regexes,
location/remote rules, salary floor, blocklisted companies, approval_mode,
active_hours, per-domain rate limits.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from src.contracts.exceptions import ConfigError

ApprovalMode = Literal["auto", "approve_each", "approve_batch"]
RemotePolicy = Literal["remote_only", "hybrid_ok", "onsite_ok", "any"]


class ActiveHours(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start: str = Field(default="08:00", description="HH:MM, 24h, local to `timezone`")
    end: str = Field(default="20:00", description="HH:MM, 24h, local to `timezone`")
    timezone: str = Field(default="UTC")

    @field_validator("start", "end")
    @classmethod
    def _valid_hhmm(cls, v: str) -> str:
        if not re.fullmatch(r"[0-2][0-9]:[0-5][0-9]", v):
            raise ValueError(f"expected HH:MM, got {v!r}")
        hour, minute = (int(x) for x in v.split(":"))
        if hour > 23 or minute > 59:
            raise ValueError(f"invalid time of day: {v!r}")
        return v


class LocationRules(BaseModel):
    model_config = ConfigDict(extra="forbid")

    remote_policy: RemotePolicy = "any"
    allowed_locations: list[str] = Field(default_factory=list)
    disallowed_locations: list[str] = Field(default_factory=list)


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_applications_per_day: int = Field(..., gt=0)
    min_match_score: int = Field(..., ge=0, le=100)
    title_include_regexes: list[str] = Field(default_factory=list)
    title_exclude_regexes: list[str] = Field(default_factory=list)
    location_rules: LocationRules = Field(default_factory=LocationRules)
    salary_floor: int | None = None
    unknown_salary_policy: Literal["accept", "reject"] = "accept"
    blocklisted_companies: list[str] = Field(default_factory=list)
    approval_mode: ApprovalMode = "approve_each"
    active_hours: ActiveHours = Field(default_factory=ActiveHours)
    per_domain_rate_limits: dict[str, int] = Field(
        default_factory=dict,
        description="requests-per-minute ceiling keyed by domain, e.g. {'greenhouse.io': 5}",
    )
    max_posting_age_days: int = Field(
        default=30,
        gt=0,
        description="freshness policy consumed by Epic 3 discovery",
    )
    max_discovery_pages: int = Field(
        default=10,
        gt=0,
        description="Epic 3 T3.1.2: scroll-and-settle pagination cap for the jobs feed "
        "(no-new-cards termination is checked first; this is the hard backstop)",
    )
    discovery_enrichment_score_threshold: int = Field(
        default=50,
        ge=0,
        le=100,
        description="Epic 3 T3.2.2: coarse match-score gate for opening a job's detail page "
        "during discovery (cheap card-only ingestion below this, full enrichment at/above "
        "it). Deliberately separate from and looser than `min_match_score`, which is Epic "
        "4 Selection's stricter apply-worthiness gate - discovery wants to enrich anything "
        "plausibly interesting, not just what will ultimately be selected.",
    )
    max_login_failures: int = Field(
        default=3,
        gt=0,
        description="Epic 2 T2.3.3: hard-stop threshold for consecutive login failures",
    )
    login_backoff_base_seconds: float = Field(
        default=2.0,
        gt=0,
        description="Epic 2 T2.3.3: base seconds for exponential backoff+jitter between "
        "login retries",
    )

    # Epic 4 (Selection Engine) T4.2.1: composite-score weights. Defaults sum
    # to 1.0 (validated below) so the composite score stays a 0..1-ish scale
    # matching each individual sub-score's own 0..1 normalization (see
    # src/selection/scoring.py:composite_score()).
    score_weight_match: float = Field(
        default=0.4,
        ge=0,
        le=1,
        description="Weight for JobRight's own match_score in the composite score",
    )
    score_weight_keyword_overlap: float = Field(
        default=0.25,
        ge=0,
        le=1,
        description="Weight for the profile.skills-vs-description/title keyword "
        "overlap ratio in the composite score",
    )
    score_weight_recency: float = Field(
        default=0.15,
        ge=0,
        le=1,
        description="Weight for posting recency (exponential decay by age, scaled "
        "by max_posting_age_days) in the composite score",
    )
    score_weight_salary_fit: float = Field(
        default=0.2,
        ge=0,
        le=1,
        description="Weight for how comfortably the job's salary clears "
        "salary_floor in the composite score",
    )

    # Epic 4 T4.2.2: optional LLM-rationale pass. Feature-flagged and
    # off by default - src/selection/scoring.py:rank_jobs() must (and does)
    # produce identical scores/ranking whether or not this is enabled/a
    # rationale_fn is supplied.
    enable_llm_rationale: bool = Field(
        default=False,
        description="Feature flag for the optional top-K LLM 'why this fits / "
        "risks' rationale pass (S4.2.2). Pipeline runs unchanged when False.",
    )
    llm_rationale_top_k: int = Field(
        default=5,
        gt=0,
        description="How many top-ranked survivors get an LLM rationale when "
        "enable_llm_rationale is True and a rationale_fn is supplied",
    )

    @field_validator("title_include_regexes", "title_exclude_regexes")
    @classmethod
    def _valid_regexes(cls, patterns: list[str]) -> list[str]:
        for pattern in patterns:
            try:
                re.compile(pattern)
            except re.error as exc:
                raise ValueError(f"invalid regex {pattern!r}: {exc}") from exc
        return patterns

    @model_validator(mode="after")
    def _score_weights_sum_to_one(self) -> Settings:
        total = (
            self.score_weight_match
            + self.score_weight_keyword_overlap
            + self.score_weight_recency
            + self.score_weight_salary_fit
        )
        if abs(total - 1.0) > 1e-6:
            raise ValueError(
                "score_weight_match + score_weight_keyword_overlap + "
                "score_weight_recency + score_weight_salary_fit must sum to 1.0 "
                f"(got {total!r})"
            )
        return self


def load_settings(path: str | Path) -> Settings:
    """Load and validate settings.yaml. Raises ConfigError with a clear
    message on any structural or validation failure (fail-fast)."""
    p = Path(path)
    if not p.exists():
        raise ConfigError(f"settings file not found: {p}")
    try:
        raw = yaml.safe_load(p.read_text()) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"settings file is not valid YAML: {p}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigError(f"settings file must contain a mapping at the top level: {p}")
    try:
        return Settings(**raw)
    except Exception as exc:  # pydantic.ValidationError, re-raised with context
        raise ConfigError(f"settings file failed validation: {p}: {exc}") from exc
