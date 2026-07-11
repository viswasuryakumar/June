from pathlib import Path

import pydantic
import pytest
from src.contracts.models import ApplicationRecord, HITLTicket, Job
from src.contracts.schema_export import export_json_schemas


def test_job_model_minimal_construction():
    job = Job(
        job_id="jr-123",
        title="Backend Engineer",
        company="Acme",
        location="Remote",
        jobright_url="https://jobright.ai/jobs/jr-123",
    )
    assert job.remote_type == "unknown"
    assert job.apply_mode == "unknown"
    assert job.raw_description == ""


def test_application_record_defaults():
    record = ApplicationRecord(job_id="jr-123")
    assert record.status == "discovered"
    assert record.attempts == 0
    assert record.screenshots == []
    assert record.timestamps == {}


def test_hitl_ticket_requires_kind():
    ticket = HITLTicket(ticket_id="t-1", job_id="jr-123", kind="captcha", context={"url": "x"})
    assert ticket.resolution is None
    assert ticket.kind == "captcha"


def test_extra_fields_forbidden():
    with pytest.raises(pydantic.ValidationError):
        Job(
            job_id="jr-1",
            title="t",
            company="c",
            location="l",
            jobright_url="u",
            not_a_real_field="oops",
        )


def test_export_json_schemas(tmp_path: Path):
    written = export_json_schemas(tmp_path)
    names = {p.name for p in written}
    assert names == {
        "Job.schema.json",
        "ApplicationRecord.schema.json",
        "HITLTicket.schema.json",
    }
    for p in written:
        assert p.exists()
        assert p.stat().st_size > 0
