"""Loads the JSON Schemas under contracts/ and validates messages against them.

Schemas are addressed by their `$id`. Relative `$ref`s are resolved through a registry backed by
the files on disk, so the same code works from the repo, from a Lambda zip and from the container
image (all of which carry a `contracts/` directory next to the code, see Taskfile `build`).
"""

from __future__ import annotations

import json
import os
from functools import cache
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import ValidationError
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

SCHEMA_BASE = "https://contracts.event-platform.local/schemas/"


class ContractViolationError(ValueError):
    """A message does not satisfy its contract."""

    def __init__(self, schema: str, errors: list[str]) -> None:
        self.schema = schema
        self.errors = errors
        super().__init__(f"{schema}: " + "; ".join(errors))


def contracts_dir() -> Path:
    """Locate contracts/: env override, then walk up from this file (repo or bundle layout)."""
    override = os.environ.get("CONTRACTS_DIR")
    if override:
        return Path(override)
    here = Path(__file__).resolve()
    for parent in [here.parent, *here.parents]:
        candidate = parent / "contracts"
        if (candidate / "schemas" / "envelope.schema.json").exists():
            return candidate
    raise FileNotFoundError("contracts/ directory not found; set CONTRACTS_DIR")


def _retrieve(uri: str) -> Resource[Any]:
    if not uri.startswith(SCHEMA_BASE):
        raise LookupError(f"unknown schema uri {uri}")
    path = contracts_dir() / "schemas" / uri.removeprefix(SCHEMA_BASE)
    with path.open(encoding="utf-8") as fh:
        return Resource.from_contents(json.load(fh), default_specification=DRAFT202012)


@cache
def registry() -> Registry[Any]:
    return Registry(retrieve=_retrieve)  # type: ignore[call-arg]


@cache
def validator(relative_path: str) -> Draft202012Validator:
    """Validator for a schema path relative to contracts/schemas.

    Example: ``validator("events/PaymentReceived.schema.json")``.
    """
    uri = SCHEMA_BASE + relative_path
    resource = _retrieve(uri)
    return Draft202012Validator(
        resource.contents,
        registry=registry().with_resource(uri, resource),
        format_checker=FormatChecker(),
    )


def validate(relative_path: str, instance: Any) -> None:
    """Raise ContractViolation listing every error, or return silently."""
    errors = sorted(validator(relative_path).iter_errors(instance), key=lambda e: list(e.path))
    if errors:
        raise ContractViolationError(relative_path, [_describe(e) for e in errors])


def _describe(error: ValidationError) -> str:
    location = "/".join(str(p) for p in error.absolute_path) or "<root>"
    return f"{location}: {error.message}"


def schema_for_envelope(kind: str, type_name: str) -> str:
    folder = "events" if kind == "event" else "commands"
    return f"{folder}/{type_name}.schema.json"
