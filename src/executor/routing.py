"""Post-apply-click destination routing (spec EPIC 6 routing + S6.2.1).

Clicking JobRight's Apply button (live-verified 2026-07-14, see
PROGRESS.md) opens the real posting in a new tab. Where that tab lands
decides the fill strategy:

- ``linkedin_login``  — LinkedIn sign-in wall. Credentials are typed by
  the human (this project never automates third-party logins); the
  driver pauses, then continues filling once logged in.
- ``linkedin``        — an authenticated LinkedIn job page (Easy Apply).
- ``external_ats``    — a company/ATS page. Preferred fill: JobRight
  extension autofill, verified + gap-filled from the application-data
  store; if the extension doesn't act, native fill from the store.
- ``jobright``        — stayed on jobright.ai (Agent-style apply).
- ``unknown``         — none of the above; native fill is still
  attempted, but the driver flags it for supervision.

Detection is deliberately URL-and-generic-DOM based, not registry-keyed:
these are arbitrary third-party sites, so per-site selector registry
entries can't exist ahead of time (the registry stays the source of
truth for jobright.ai's own UI).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal
from urllib.parse import urlparse

DestinationKind = Literal[
    "linkedin_login",
    "linkedin",
    "external_ats",
    "jobright",
    "unknown",
]

# Hosts of ATS platforms we expect to see; membership only affects the
# `ats` label on the result (useful for recorded answers/reporting), not
# the routing decision itself.
KNOWN_ATS_HOSTS: dict[str, str] = {
    "greenhouse.io": "greenhouse",
    "boards.greenhouse.io": "greenhouse",
    "job-boards.greenhouse.io": "greenhouse",
    "lever.co": "lever",
    "jobs.lever.co": "lever",
    "myworkdayjobs.com": "workday",
    "ashbyhq.com": "ashby",
    "jobs.ashbyhq.com": "ashby",
    "icims.com": "icims",
    "smartrecruiters.com": "smartrecruiters",
    "workable.com": "workable",
    "bamboohr.com": "bamboohr",
    "jazz.co": "jazzhr",
    "taleo.net": "taleo",
}

_LOGIN_HINT_SELECTORS = (
    "input[type=password]",
    "form[action*='login']",
    "form[action*='signin']",
    "form[action*='checkpoint']",  # LinkedIn's auth wall posts to /checkpoint/...
)


@dataclass(frozen=True)
class ApplyDestination:
    kind: DestinationKind
    url: str
    host: str
    ats: str | None = None  # known-ATS label when recognizable from the host


def _matches_host(host: str, suffix: str) -> bool:
    return host == suffix or host.endswith("." + suffix)


def detect_ats(host: str) -> str | None:
    for known_host, label in KNOWN_ATS_HOSTS.items():
        if _matches_host(host, known_host):
            return label
    return None


def has_login_wall(page, *, timeout_ms: int = 2000) -> bool:
    """Generic 'this page wants a sign-in' check: a visible password
    field (or a login/checkpoint form) anywhere on the page."""
    for selector in _LOGIN_HINT_SELECTORS:
        try:
            locator = page.locator(selector).first
            if locator.count() and locator.is_visible(timeout=timeout_ms):
                return True
        except Exception:
            continue
    return False


def classify_apply_destination(page, *, login_check_timeout_ms: int = 2000) -> ApplyDestination:
    """Classify the tab the Apply click opened. Never raises — an
    unreadable page degrades to kind='unknown' so the driver can still
    show it to the human instead of crashing the run."""
    try:
        url = page.url
    except Exception:
        return ApplyDestination(kind="unknown", url="", host="")
    host = (urlparse(url).hostname or "").casefold()

    if _matches_host(host, "jobright.ai"):
        return ApplyDestination(kind="jobright", url=url, host=host)

    if _matches_host(host, "linkedin.com"):
        needs_login = "/login" in url or "/checkpoint" in url or "/authwall" in url
        if not needs_login:
            needs_login = has_login_wall(page, timeout_ms=login_check_timeout_ms)
        kind: DestinationKind = "linkedin_login" if needs_login else "linkedin"
        return ApplyDestination(kind=kind, url=url, host=host, ats="linkedin")

    if host:
        return ApplyDestination(kind="external_ats", url=url, host=host, ats=detect_ats(host))
    return ApplyDestination(kind="unknown", url=url, host=host)
