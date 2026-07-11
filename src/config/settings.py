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
from pydantic import BaseModel, ConfigDict, Field, field_validator

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

    @field_validator("title_include_regexes", "title_exclude_regexes")
    @classmethod
    def _valid_regexes(cls, patterns: list[str]) -> list[str]:
        for pattern in patterns:
            try:
                re.compile(pattern)
            except re.error as exc:
                raise ValueError(f"invalid regex {pattern!r}: {exc}") from exc
        return patterns


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
