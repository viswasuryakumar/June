"""Shared exception types used across module boundaries."""

from __future__ import annotations


class JuneError(Exception):
    """Base class for all June pipeline errors."""


class InvalidTransition(JuneError):
    """Raised when a tracker transition violates the state machine (spec §3.2)."""

    def __init__(self, job_id: str, from_status: str, to_status: str):
        self.job_id = job_id
        self.from_status = from_status
        self.to_status = to_status
        super().__init__(f"invalid transition for job {job_id!r}: {from_status!r} -> {to_status!r}")


class SecretsError(JuneError):
    """Raised when required secrets are missing or invalid at startup."""


class ConfigError(JuneError):
    """Raised when settings.yaml / profile.yaml fail validation."""


class LoginFailed(JuneError):
    """Raised when the login flow has failed `max_failures` consecutive
    times (spec T2.3.3 / Epic 2 session lifecycle).

    Hard-stops the auto-relogin loop rather than continuing to hammer the
    login endpoint; the caller (orchestrator, once it exists) should
    notify and halt the run rather than retry further.
    """

    def __init__(self, attempts: int, max_failures: int):
        self.attempts = attempts
        self.max_failures = max_failures
        super().__init__(
            f"login failed {attempts} consecutive time(s); hard-stop at {max_failures}"
        )


class SelectorBroken(JuneError):
    """Raised when a registered selector fails to resolve on a live page.

    Carries enough context to open a HITLTicket of kind 'selector_broken'.
    """

    def __init__(
        self,
        selector_key: str,
        *,
        page_url: str | None = None,
        snapshot_path: str | None = None,
        original_error: str | None = None,
    ):
        self.selector_key = selector_key
        self.page_url = page_url
        self.snapshot_path = snapshot_path
        self.original_error = original_error
        self.ticket_kind = "selector_broken"
        msg = f"selector {selector_key!r} failed to resolve"
        if page_url:
            msg += f" on {page_url}"
        if snapshot_path:
            msg += f" (snapshot: {snapshot_path})"
        super().__init__(msg)
