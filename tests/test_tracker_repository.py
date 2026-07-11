import pytest
from src.contracts.exceptions import InvalidTransition
from src.contracts.models import HITLTicket, Job
from src.tracker.repository import InMemoryTrackerRepository, is_transition_allowed


def _job(job_id="jr-1") -> Job:
    return Job(
        job_id=job_id,
        title="Backend Engineer",
        company="Acme",
        location="Remote",
        jobright_url="https://jobright.ai/jobs/" + job_id,
    )


def test_add_job_is_idempotent():
    tracker = InMemoryTrackerRepository()
    tracker.add_job(_job())
    tracker.add_job(_job())
    assert len(tracker.get_jobs()) == 1
    assert tracker.get_jobs("discovered")[0].job_id == "jr-1"


def test_get_jobs_filters_by_status():
    tracker = InMemoryTrackerRepository()
    tracker.add_job(_job("jr-1"))
    tracker.add_job(_job("jr-2"))
    tracker.transition("jr-1", "selected")
    assert [r.job_id for r in tracker.get_jobs("selected")] == ["jr-1"]
    assert [r.job_id for r in tracker.get_jobs("discovered")] == ["jr-2"]


def test_valid_transition_chain():
    tracker = InMemoryTrackerRepository()
    tracker.add_job(_job())
    tracker.transition("jr-1", "selected")
    tracker.transition("jr-1", "resume_tailored", {"resume_variant_path": "/tmp/r.pdf"})
    tracker.transition("jr-1", "applying")
    tracker.transition("jr-1", "needs_human")
    tracker.transition("jr-1", "applying")
    record = tracker.transition("jr-1", "submitted")
    assert record.status == "submitted"
    assert record.resume_variant_path == "/tmp/r.pdf"
    assert "submitted" in record.timestamps


def test_invalid_transition_raises():
    tracker = InMemoryTrackerRepository()
    tracker.add_job(_job())
    with pytest.raises(InvalidTransition):
        tracker.transition("jr-1", "submitted")


def test_terminal_states_have_no_outgoing_transitions():
    for terminal in ("submitted", "failed", "skipped"):
        assert is_transition_allowed(terminal, "selected") is False


def test_transition_unknown_job_raises_keyerror():
    tracker = InMemoryTrackerRepository()
    with pytest.raises(KeyError):
        tracker.transition("does-not-exist", "selected")


def test_events_are_recorded():
    tracker = InMemoryTrackerRepository()
    tracker.add_job(_job())
    tracker.transition("jr-1", "selected")
    events = tracker.get_events("jr-1")
    assert len(events) == 2
    assert events[0]["to_status"] == "discovered"
    assert events[1]["to_status"] == "selected"


def test_ticket_store_roundtrip():
    tracker = InMemoryTrackerRepository()
    ticket = HITLTicket(ticket_id="t-1", job_id="jr-1", kind="captcha", context={})
    tracker.add_ticket(ticket)
    assert tracker.get_ticket("t-1") == ticket
    assert tracker.get_ticket("missing") is None
    assert tracker.list_tickets() == [ticket]
