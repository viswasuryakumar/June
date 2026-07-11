from __future__ import annotations

import json
from pathlib import Path

SCHEMA_DIR = Path(__file__).with_name("schemas")


def load_schema(name: str) -> dict:
    return json.loads((SCHEMA_DIR / f"{name}.json").read_text(encoding="utf-8"))


def unwrap_claude_json(raw: str) -> dict:
    """Accept Claude's structured output wrapper or a direct JSON object."""
    data = json.loads(raw)
    if isinstance(data, dict) and isinstance(data.get("structured_output"), dict):
        return data["structured_output"]
    if isinstance(data, dict) and isinstance(data.get("result"), str):
        try:
            nested = json.loads(data["result"])
        except json.JSONDecodeError:
            pass
        else:
            if isinstance(nested, dict):
                return nested
    if not isinstance(data, dict):
        raise ValueError("agent output must be a JSON object")
    return data
