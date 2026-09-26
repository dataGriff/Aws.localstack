"""Pure archive-writer logic: parse -> group by table -> Arrow -> append with commit retry."""

from __future__ import annotations

import json
import random
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import pyarrow as pa
from pyiceberg.catalog import Catalog
from pyiceberg.exceptions import CommitFailedException

from iceberg.schema import ARROW_SCHEMA


class UnparseableRecordError(ValueError):
    """The SQS body is not an archivable EventBridge event."""


@dataclass(frozen=True)
class ArchiveRecord:
    event_id: str
    bus: str
    source: str
    detail_type: str
    kind: str | None
    type: str | None
    correlation_id: str | None
    schema_version: str | None
    event_time: datetime
    ingested_at: datetime
    detail: str


def _parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def parse_record(body: str, *, ingested_at: datetime, known_buses: Iterable[str]) -> ArchiveRecord:
    """Parse one SQS body produced by the archive rules' input transformer: {bus, event}."""
    try:
        wrapper = json.loads(body)
    except json.JSONDecodeError as exc:
        raise UnparseableRecordError(f"body is not JSON: {exc}") from exc
    if not isinstance(wrapper, dict) or "bus" not in wrapper or "event" not in wrapper:
        raise UnparseableRecordError("body must be an object with 'bus' and 'event'")
    bus = wrapper["bus"]
    if bus not in set(known_buses):
        raise UnparseableRecordError(f"unknown bus {bus!r}")
    event = wrapper["event"]
    if not isinstance(event, dict):
        raise UnparseableRecordError("'event' is not an object")
    detail = event.get("detail")
    if not isinstance(detail, dict):
        raise UnparseableRecordError("'event.detail' is not an object")
    for key in ("id", "source", "detail-type", "time"):
        if not isinstance(event.get(key), str) or not event[key]:
            raise UnparseableRecordError(f"'event.{key}' missing")

    event_time = _parse_time(detail.get("occurredAt")) or _parse_time(event["time"])
    if event_time is None:
        raise UnparseableRecordError("no usable event time")

    def opt(key: str) -> str | None:
        value = detail.get(key)
        return value if isinstance(value, str) else None

    return ArchiveRecord(
        event_id=event["id"],
        bus=bus,
        source=event["source"],
        detail_type=event["detail-type"],
        kind=opt("kind"),
        type=opt("type"),
        correlation_id=opt("correlationId"),
        schema_version=opt("schemaVersion"),
        event_time=event_time.astimezone(UTC),
        ingested_at=ingested_at.astimezone(UTC),
        detail=json.dumps(detail, separators=(",", ":"), sort_keys=True),
    )


def group_by_table(
    records: Iterable[ArchiveRecord], tables_by_bus: dict[str, str]
) -> dict[str, list[ArchiveRecord]]:
    groups: dict[str, list[ArchiveRecord]] = {}
    for record in records:
        groups.setdefault(tables_by_bus[record.bus], []).append(record)
    return groups


def to_arrow(records: list[ArchiveRecord]) -> pa.Table:
    columns = {name: [getattr(r, name) for r in records] for name in ARROW_SCHEMA.names}
    return pa.Table.from_pydict(columns, schema=ARROW_SCHEMA)


def append_with_retry(
    catalog: Catalog,
    identifier: tuple[str, str],
    data: pa.Table,
    *,
    max_attempts: int = 6,
    base_delay: float = 0.5,
    sleep: Callable[[float], None] = time.sleep,
    on_retry: Callable[[int, Exception], None] | None = None,
) -> None:
    """One append() per table per batch; reload and retry with backoff on commit conflicts."""
    attempt = 0
    while True:
        attempt += 1
        table = catalog.load_table(identifier)
        try:
            table.append(data)
            return
        except CommitFailedException as exc:
            if attempt >= max_attempts:
                raise
            if on_retry:
                on_retry(attempt, exc)
            sleep(base_delay * (2 ** (attempt - 1)) * (1 + random.random() * 0.5))
