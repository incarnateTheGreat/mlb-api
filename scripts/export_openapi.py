"""Export FastAPI OpenAPI schema for contract sharing/artifacts."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _ensure_minimal_settings() -> None:
    """Set minimal env required to import app settings during schema export."""
    os.environ.setdefault(
        "DATABASE_URL",
        "postgresql://placeholder:placeholder@localhost:5432/placeholder",
    )


def main() -> None:
    _ensure_minimal_settings()

    # Import after env defaults are applied so Settings validation succeeds in CI.
    from app.main import app

    output_path = Path("openapi.json")
    schema = app.openapi()
    output_path.write_text(json.dumps(schema, indent=2, sort_keys=True), encoding="utf-8")
    print(f"Wrote OpenAPI schema to {output_path}")


if __name__ == "__main__":
    main()
