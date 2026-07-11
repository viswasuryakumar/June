import io
import json
import logging

from src.observability.logging import configure_logging, log_event


def _configure(stream, secrets):
    # Use a fresh logger name per test to avoid handler bleed between tests.
    logger = configure_logging(
        "run-test-1", secret_values=secrets, stream=stream, logger_name="june.test"
    )
    return logger


def test_log_line_is_valid_json_with_expected_fields():
    stream = io.StringIO()
    logger = _configure(stream, secrets=[])
    log_event(logger, "job_processed", job_id="jr-1", duration=1.23, extra_field="ok")
    line = stream.getvalue().strip()
    payload = json.loads(line)
    assert payload["run_id"] == "run-test-1"
    assert payload["job_id"] == "jr-1"
    assert payload["step"] == "job_processed"
    assert payload["duration"] == 1.23
    assert payload["extra_field"] == "ok"
    assert payload["level"] == "INFO"


def test_secret_value_is_redacted_from_message():
    stream = io.StringIO()
    logger = _configure(stream, secrets=["hunter2"])
    log_event(logger, "login_attempt", message="logging in with password hunter2")
    line = stream.getvalue()
    assert "hunter2" not in line
    assert "***REDACTED***" in line


def test_secret_value_is_redacted_from_extra_fields():
    stream = io.StringIO()
    logger = _configure(stream, secrets=["s3cr3t-token"])
    log_event(logger, "notify", channel_token="s3cr3t-token")
    line = stream.getvalue()
    assert "s3cr3t-token" not in line
    assert "***REDACTED***" in line


def test_no_secrets_configured_passes_through_untouched():
    stream = io.StringIO()
    logger = _configure(stream, secrets=[])
    log_event(logger, "plain_event", note="hello world")
    payload = json.loads(stream.getvalue().strip())
    assert payload["note"] == "hello world"


def test_error_level_event():
    stream = io.StringIO()
    logger = _configure(stream, secrets=[])
    log_event(logger, "pipeline_failed", level=logging.ERROR, error="boom")
    payload = json.loads(stream.getvalue().strip())
    assert payload["level"] == "ERROR"
    assert payload["error"] == "boom"
