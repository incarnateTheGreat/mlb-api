"""Export FastAPI OpenAPI schema for contract sharing/artifacts."""

from __future__ import annotations

import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.main import app


def main() -> None:
    output_path = Path("openapi.json")
    schema = app.openapi()
    output_path.write_text(json.dumps(schema, indent=2, sort_keys=True), encoding="utf-8")
    print(f"Wrote OpenAPI schema to {output_path}")


if __name__ == "__main__":
    main()
