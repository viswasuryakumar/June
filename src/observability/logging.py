"""Structured JSON logging with secret/PII redaction (spec T1.4 / S1.4.1).

Every log line is a single JSON object with at least:
    timestamp, level, message, run_id, job_id, step, duration

Secrets are redacted at the handler level via `RedactionFilter`, so no
call site can accidentally leak a credential even if it tries - the
filter runs on every record before formatting/output.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any

_STANDARD_LOG_RECORD_ATTRS = frozenset(vars(logging.makeLogRecord({})).keys())
REDACTED_PLACEHOLDER = "***REDACTED***"


class RedactionFilter(logging.Filter):
    """Scrubs configured secret literal values out of every field of a
    LogRecord (message, args, and any structured `extra` attributes)
    before it reaches a handler's formatter.
    """

    def __init__(self, secret_values: list[str] | None = None):
        super().__init__()
        # Longest-first so a password that happens to be a substring of
        # another secret doesn't get partially redacted.
        self._secrets = sorted({s for s in (secret_values or []) if s}, key=len, reverse=True)

    def add_secret(self, value: str) -> None:
        if value and value not in self._secrets:
            self._secrets.append(value)
            self._secrets.sort(key=len, reverse=True)

    def _redact(self, value: Any) -> Any:
        if isinstance(value, str):
            redacted = value
            for secret in self._secrets:
                redacted = redacted.replace(secret, REDACTED_PLACEHOLDER)
            return redacted
        if isinstance(value, dict):
            return {k: self._redact(v) for k, v in value.items()}
        if isinstance(value, list | tuple):
            return type(value)(self._redact(v) for v in value)
        return value

    def filter(self, record: logging.LogRecord) -> bool:
        if not self._secrets:
            return True
        record.msg = self._redact(record.getMessage())
        record.args = ()
        for key, val in list(record.__dict__.items()):
            if key in _STANDARD_LOG_RECORD_ATTRS:
                continue
            record.__dict__[key] = self._redact(val)
        return True


class JsonFormatter(logging.Formatter):
    """Renders a LogRecord as a single-line JSON object."""

    def __init__(self, run_id: str | None = None):
        super().__init__()
        self._run_id = run_id

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "run_id": getattr(record, "run_id", self._run_id),
            "job_id": getattr(record, "job_id", None),
            "step": getattr(record, "step", None),
            "duration": getattr(record, "duration", None),
        }
        for key, val in record.__dict__.items():
            if key in _STANDARD_LOG_RECORD_ATTRS or key in payload:
                continue
            payload[key] = val
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging(
    run_id: str,
    *,
    secret_values: list[str] | None = None,
    level: int = logging.INFO,
    stream=None,
    logger_name: str = "june",
) -> logging.Logger:
    """Configure (and return) the shared 'june' structured logger.

    Idempotent: safe to call multiple times (e.g. once per CLI invocation)
    without stacking duplicate handlers.
    """
    logger = logging.getLogger(logger_name)
    logger.setLevel(level)
    logger.propagate = False

    for handler in list(logger.handlers):
        logger.removeHandler(handler)

    handler = logging.StreamHandler(stream or sys.stdout)
    handler.setFormatter(JsonFormatter(run_id=run_id))
    handler.addFilter(RedactionFilter(secret_values))
    logger.addHandler(handler)
    return logger


def log_event(
    logger: logging.Logger,
    step: str,
    *,
    job_id: str | None = None,
    duration: float | None = None,
    level: int = logging.INFO,
    message: str | None = None,
    **extra: Any,
) -> None:
    """Emit one structured event line: run_id/job_id/step/duration + extras."""
    logger.log(
        level,
        message or step,
        extra={"job_id": job_id, "step": step, "duration": duration, **extra},
    )
