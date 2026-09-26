"""Fake third-party generator: replays fixture payloads onto the ingress bus."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import boto3

from domain.registry import THIRD_PARTY_PROVIDER
from shared.config import service_name
from shared.eventbridge import publish

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
ALL_SETS = ("valid", "duplicates", "out_of_order", "invalid")


def ingress_source() -> str:
    return f"{service_name()}.thirdparty.{THIRD_PARTY_PROVIDER}"


def load_set(name: str, fixtures_dir: Path = FIXTURES_DIR) -> list[dict[str, Any]]:
    payloads = []
    for path in sorted((fixtures_dir / name).glob("*.json")):
        with path.open(encoding="utf-8") as fh:
            payloads.append(json.load(fh))
    return payloads


def freshen(payload: dict[str, Any], suffix: str | None = None) -> dict[str, Any]:
    """Return a copy with provider ids suffixed so repeated runs never collide on dedupe.

    Duplicates within one run keep colliding because the same suffix is applied to equal ids.
    """
    suffix = suffix or uuid.uuid4().hex[:10]
    clone = json.loads(json.dumps(payload))
    if isinstance(clone.get("id"), str):
        clone["id"] = f"{clone['id']}_{suffix}"
    obj = clone.get("data", {}).get("object") if isinstance(clone.get("data"), dict) else None
    if isinstance(obj, dict):
        for key in ("id", "invoice"):
            if isinstance(obj.get(key), str):
                obj[key] = f"{obj[key]}_{suffix}"
    return clone


def mark_synthetic(payload: dict[str, Any]) -> dict[str, Any]:
    clone = dict(payload)
    clone["synthetic"] = True
    if isinstance(clone.get("id"), str) and not clone["id"].startswith("evt_synthetic_"):
        clone["id"] = "evt_synthetic_" + clone["id"].removeprefix("evt_")
    return clone


def to_entry(bus_name: str, payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "EventBusName": bus_name,
        "Source": ingress_source(),
        "DetailType": str(payload.get("type") or "unknown"),
        "Time": datetime.now(tz=UTC),
        "Detail": json.dumps(payload, separators=(",", ":")),
    }


def send(bus_name: str, payloads: list[dict[str, Any]], client: Any = None) -> int:
    client = client or boto3.client("events")
    entries = [to_entry(bus_name, p) for p in payloads]
    publish(client, entries)
    return len(entries)
