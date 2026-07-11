"""Secrets loading (spec T1.2 / S1.2.3).

JOBRIGHT_EMAIL, JOBRIGHT_PASSWORD (or an SSO flag) and notifier tokens are
read from environment variables only (optionally populated from a local
.env file via python-dotenv for developer convenience - .env is
git-ignored and never committed). Validation is fail-fast: a missing
required secret raises SecretsError immediately at startup, before any
browser/network action.

Secret values must never be logged. `Secrets.redaction_values()` feeds the
observability redaction filter (src/observability/logging.py) so this is
enforced structurally rather than by convention alone.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

from src.contracts.exceptions import SecretsError

REQUIRED_ENV_VARS = ("JOBRIGHT_EMAIL", "JOBRIGHT_PASSWORD")
OPTIONAL_ENV_VARS = ("JOBRIGHT_SSO", "NOTIFIER_SLACK_WEBHOOK_URL", "NOTIFIER_TELEGRAM_TOKEN")


@dataclass(frozen=True)
class Secrets:
    jobright_email: str
    jobright_password: str
    jobright_sso: bool = False
    notifier_slack_webhook_url: str | None = None
    notifier_telegram_token: str | None = None

    def redaction_values(self) -> list[str]:
        """Every literal secret value that must never appear in a log line."""
        values = [self.jobright_email, self.jobright_password]
        values += [v for v in (self.notifier_slack_webhook_url, self.notifier_telegram_token) if v]
        return [v for v in values if v]

    def __repr__(self) -> str:  # never let a stray repr()/print() leak a secret
        return "Secrets(***REDACTED***)"

    __str__ = __repr__


def load_secrets(env_file: str | None = ".env", *, require: bool = True) -> Secrets:
    """Load secrets from the environment (and optionally a .env file).

    Fail-fast: raises SecretsError immediately if a required variable is
    missing or empty, unless `require=False` (used by non-live code paths
    like `discover-only`/dry-run scaffolding where credentials genuinely
    aren't needed yet).
    """
    if env_file:
        load_dotenv(env_file, override=False)

    missing = [name for name in REQUIRED_ENV_VARS if not os.environ.get(name)]
    if missing and require:
        raise SecretsError(
            "missing required secret(s): "
            + ", ".join(missing)
            + ". Set them as environment variables (see .env.example)."
        )

    return Secrets(
        jobright_email=os.environ.get("JOBRIGHT_EMAIL", ""),
        jobright_password=os.environ.get("JOBRIGHT_PASSWORD", ""),
        jobright_sso=os.environ.get("JOBRIGHT_SSO", "").lower() in {"1", "true", "yes"},
        notifier_slack_webhook_url=os.environ.get("NOTIFIER_SLACK_WEBHOOK_URL") or None,
        notifier_telegram_token=os.environ.get("NOTIFIER_TELEGRAM_TOKEN") or None,
    )
