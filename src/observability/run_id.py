"""Run id generation. Centralized so every module tags logs/artifacts
consistently under runs/<run_id>/."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime


def new_run_id() -> str:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    short = uuid.uuid4().hex[:8]
    return f"{stamp}-{short}"
