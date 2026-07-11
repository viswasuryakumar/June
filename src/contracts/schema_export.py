"""Export JSON Schemas for the shared contracts so non-Python agents/tools
can validate against the same data shapes (spec T1.3 / S1.3.1).
"""

from __future__ import annotations

import json
from pathlib import Path

from src.contracts.models import ALL_MODELS


def export_json_schemas(output_dir: Path | str) -> list[Path]:
    """Write one <ModelName>.schema.json file per contract model.

    Returns the list of file paths written.
    """
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for model in ALL_MODELS:
        schema = model.model_json_schema()
        path = out / f"{model.__name__}.schema.json"
        path.write_text(json.dumps(schema, indent=2, sort_keys=True) + "\n")
        written.append(path)
    return written


if __name__ == "__main__":
    default_out = Path(__file__).resolve().parents[2] / "contracts" / "schemas"
    paths = export_json_schemas(default_out)
    for p in paths:
        print(f"wrote {p}")
