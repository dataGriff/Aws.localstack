"""Helpers shared by the generator, smoke test, query tool and integration tests."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]


def outputs_file(env_name: str) -> Path:
    override = os.environ.get("OUTPUTS_FILE")
    return Path(override) if override else REPO_ROOT / "build" / f"outputs.{env_name}.json"


def stack_outputs(env_name: str) -> dict[str, str]:
    """Merged CloudFormation outputs written by `cdk deploy --outputs-file`. Keys are unique."""
    path = outputs_file(env_name)
    if not path.exists():
        raise FileNotFoundError(f"{path} not found; run `task local:deploy` or `task deploy ENV=…`")
    with path.open(encoding="utf-8") as fh:
        data: dict[str, dict[str, str]] = json.load(fh)
    merged: dict[str, str] = {}
    for stack, values in data.items():
        for key, value in values.items():
            if key in merged and merged[key] != value:
                raise ValueError(f"duplicate output key {key} in {stack}")
            merged[key] = value
    return merged


def output(env_name: str, key: str) -> str:
    """One output; an environment variable named like the key (UPPER_SNAKE) overrides it."""
    env_key = "".join(f"_{c}" if c.isupper() else c.upper() for c in key).lstrip("_").upper()
    if env_key in os.environ:
        return os.environ[env_key]
    return stack_outputs(env_name)[key]


def load_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)
