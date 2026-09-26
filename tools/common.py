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
    """Flattened `terraform output -json` written by `task deploy` / `task local:deploy`.

    Scalar outputs keep their name. The `Commands` map is flattened per command type, e.g.
    Commands.ReconcileInvoice.QueueUrl -> ReconcileInvoiceQueueUrl.
    """
    path = outputs_file(env_name)
    if not path.exists():
        raise FileNotFoundError(f"{path} not found; run `task local:deploy` or `task deploy ENV=…`")
    with path.open(encoding="utf-8") as fh:
        data: dict[str, dict[str, Any]] = json.load(fh)
    flat: dict[str, str] = {}
    for key, entry in data.items():
        value = entry.get("value") if isinstance(entry, dict) and "value" in entry else entry
        if isinstance(value, dict):
            for command, fields in value.items():
                for field, field_value in fields.items():
                    flat[f"{command}{field}"] = str(field_value)
        elif value is not None:
            flat[key] = str(value)
    return flat


def output(env_name: str, key: str) -> str:
    """One output; an environment variable named like the key (UPPER_SNAKE) overrides it."""
    env_key = "".join(f"_{c}" if c.isupper() else c.upper() for c in key).lstrip("_").upper()
    if env_key in os.environ:
        return os.environ[env_key]
    return stack_outputs(env_name)[key]


def load_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)
