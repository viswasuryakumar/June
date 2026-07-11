"""Tracker repository interface (spec T1.3 / S1.3.2).

Every module reads/writes tracker state only through this interface.
No module talks to another module's internals - this is what enables
parallel agent development (spec §3.2).

`InMemoryTrackerRepository` is a fully-functional in-memory fake other
epics can build/test against before the real SQLite-backed implementation
(Epic 8) exists.
"""

from __future__ import annotations

import abc
from datetime import UTC, datetime

from src.contracts.exceptions import InvalidTransition
from src.contracts.models import ApplicationRecord, ApplicationStatus, HITLTicket, Job

# Spec §3.2 state machine, encoded as an explicit allow-list of edges.
# needs_human can bounce back to applying and vice versa; discovered/selected/
# resume_tailored/applying are otherwise a straight line into a terminal state.
_ALLOWED_TRANSITIONS: dict[ApplicationStatus, set[ApplicationStatus]] = {
    "discovered": {"selected", "skipped"},
    "selected": {"resume_tailored", "skipped", "failed"},
    "resume_tailored": {"applying", "skipped", "failed"},
    "applying": {"needs_human", "submitted", "failed", "skipped"},
    "needs_human": {"applying", "failed", "skipped"},
    "submitted": set(),
    "failed": set(),
    "skipped": set(),
}


def is_transition_allowed(from_status: ApplicationStatus, to_status: ApplicationStatus) -> bool:
    return to_status in _ALLOWED_TRANSITIONS.get(from_status, set())


class TrackerRepository(abc.ABC):
    """Abstract tracker interface. See spec §3.1/§3.2/T1.3."""

    @abc.abstractmethod
    def add_job(self, job: Job) -> ApplicationRecord:
        """Register a newly discovered job, creating its ApplicationRecord
        in status 'discovered' if it doesn't already exist. Idempotent."""

    @abc.abstractmethod
    def get_jobs(self, status: ApplicationStatus | None = None) -> list[ApplicationRecord]:
        """Return application records, optionally filtered by status."""

    @abc.abstractmethod
    def get_job(self, job_id: str) -> ApplicationRecord | None:
        """Return a single application record, or None if unknown."""

    @abc.abstractmethod
    def get_job_details(self, job_id: str) -> Job | None:
        """Return the underlying Job listing data, or None if unknown."""

    @abc.abstractmethod
    def transition(
        self,
        job_id: str,
        to_status: ApplicationStatus,
        meta: dict | None = None,
    ) -> ApplicationRecord:
        """Move a job's ApplicationRecord to a new status, validating the
        transition against the state machine and recording metadata
        (e.g. reason codes, error text, resume_variant_path) onto the record.
        """


class InMemoryTrackerRepository(TrackerRepository):
    """In-memory fake tracker for tests and for other epics to develop
    against before Epic 8's SQLite-backed tracker lands.
    """

    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._records: dict[str, ApplicationRecord] = {}
        self._events: list[dict] = []
        self._tickets: dict[str, HITLTicket] = {}

    # -- jobs / application records -----------------------------------

    def add_job(self, job: Job) -> ApplicationRecord:
        self._jobs[job.job_id] = job
        if job.job_id not in self._records:
            record = ApplicationRecord(
                job_id=job.job_id,
                status="discovered",
                timestamps={"discovered": datetime.now(UTC)},
            )
            self._records[job.job_id] = record
            self._log_event(
                job.job_id, actor="automation", from_status=None, to_status="discovered"
            )
        return self._records[job.job_id]

    def get_jobs(self, status: ApplicationStatus | None = None) -> list[ApplicationRecord]:
        records = list(self._records.values())
        if status is not None:
            records = [r for r in records if r.status == status]
        return records

    def get_job(self, job_id: str) -> ApplicationRecord | None:
        return self._records.get(job_id)

    def get_job_details(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)

    def transition(
        self,
        job_id: str,
        to_status: ApplicationStatus,
        meta: dict | None = None,
    ) -> ApplicationRecord:
        record = self._records.get(job_id)
        if record is None:
            raise KeyError(f"unknown job_id {job_id!r}; call add_job() first")

        from_status = record.status
        if not is_transition_allowed(from_status, to_status):
            raise InvalidTransition(job_id, from_status, to_status)

        meta = meta or {}
        updated = record.model_copy(deep=True)
        updated.status = to_status
        updated.timestamps[to_status] = datetime.now(UTC)

        if "resume_variant_path" in meta:
            updated.resume_variant_path = meta["resume_variant_path"]
        if "last_error" in meta:
            updated.last_error = meta["last_error"]
        if meta.get("increment_attempts"):
            updated.attempts += 1
        if "screenshot" in meta:
            updated.screenshots.append(meta["screenshot"])

        self._records[job_id] = updated
        self._log_event(
            job_id,
            actor=meta.get("actor", "automation"),
            from_status=from_status,
            to_status=to_status,
            meta=meta,
        )
        return updated

    # -- events (append-only audit log, spec T8.1) ----------------------

    def _log_event(
        self,
        job_id: str,
        *,
        actor: str,
        from_status: str | None,
        to_status: str,
        meta: dict | None = None,
    ) -> None:
        self._events.append(
            {
                "job_id": job_id,
                "actor": actor,
                "from_status": from_status,
                "to_status": to_status,
                "meta": meta or {},
                "at": datetime.now(UTC),
            }
        )

    def get_events(self, job_id: str | None = None) -> list[dict]:
        if job_id is None:
            return list(self._events)
        return [e for e in self._events if e["job_id"] == job_id]

    # -- HITL tickets (minimal extension for CLI stubs; Epic 7 owns the
    #    real ticket store and richer API - see PROGRESS.md deviation note) --

    def add_ticket(self, ticket: HITLTicket) -> HITLTicket:
        self._tickets[ticket.ticket_id] = ticket
        return ticket

    def get_ticket(self, ticket_id: str) -> HITLTicket | None:
        return self._tickets.get(ticket_id)

    def list_tickets(self) -> list[HITLTicket]:
        return list(self._tickets.values())
