"""Access to the generator fixture payloads from tests."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

FIXTURES = Path(__file__).resolve().parents[2] / "tools" / "generator" / "fixtures"


def fixture_files(folder: str) -> list[Path]:
    return sorted((FIXTURES / folder).glob("*.json"))


def load(path: Path) -> Any:
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


def first_valid_payload() -> dict[str, Any]:
    payload: dict[str, Any] = load(fixture_files("valid")[0])
    return payload
