"""Archive writer: parsing, grouping, Arrow conversion and appends against a real local catalog."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pyarrow as pa
import pytest
from pyiceberg.catalog.sql import SqlCatalog
from pyiceberg.exceptions import CommitFailedException

from functions.archive_writer.writer import (
    UnparseableRecordError,
    append_with_retry,
    group_by_table,
    parse_record,
    to_arrow,
)
from iceberg.schema import ARROW_SCHEMA, DEFAULT_TABLES
from iceberg.setup import ensure_tables

NOW = datetime(2025, 9, 26, 12, 0, tzinfo=UTC)


def eb_event(
    event_id: str,
    detail: dict[str, Any],
    *,
    source: str = "svc",
    detail_type: str = "x",
    time: str = "2025-09-26T10:00:00Z",
) -> dict[str, Any]:
    return {
        "version": "0",
        "id": event_id,
        "source": source,
        "detail-type": detail_type,
        "time": time,
        "region": "eu-west-1",
        "account": "0",
        "resources": [],
        "detail": detail,
    }


def body(bus: str, event: dict[str, Any]) -> str:
    return json.dumps({"bus": bus, "event": event})


def test_parse_domain_record_uses_envelope_fields_and_occurred_at() -> None:
    detail = {
        "id": "m1",
        "kind": "command",
        "type": "ReconcileInvoice",
        "schemaVersion": "1.0",
        "occurredAt": "2025-09-25T23:59:59.000Z",
        "correlationId": "c1",
        "causationId": "e1",
        "source": "evt_1",
        "data": {},
    }
    rec = parse_record(
        body("domain", eb_event("eb1", detail, detail_type="ReconcileInvoice")),
        ingested_at=NOW,
        known_buses=DEFAULT_TABLES,
    )
    assert rec.bus == "domain"
    assert (rec.kind, rec.type, rec.correlation_id, rec.schema_version) == (
        "command",
        "ReconcileInvoice",
        "c1",
        "1.0",
    )
    assert rec.event_time == datetime(2025, 9, 25, 23, 59, 59, tzinfo=UTC)
    assert json.loads(rec.detail) == detail


def test_parse_ingress_record_falls_back_to_event_time() -> None:
    rec = parse_record(
        body("ingress", eb_event("eb2", {"id": "evt_1", "created": 1})),
        ingested_at=NOW,
        known_buses=DEFAULT_TABLES,
    )
    assert rec.kind is None and rec.type is None
    assert rec.event_time == datetime(2025, 9, 26, 10, 0, tzinfo=UTC)
    assert rec.ingested_at == NOW


@pytest.mark.parametrize(
    "raw",
    [
        "not json",
        json.dumps({"event": {}}),
        json.dumps({"bus": "other", "event": eb_event("x", {})}),
        json.dumps({"bus": "ingress", "event": "nope"}),
        json.dumps({"bus": "ingress", "event": eb_event("x", "not-an-object")}),
        json.dumps({"bus": "ingress", "event": {"detail": {}}}),
    ],
)
def test_unparseable_records_raise(raw: str) -> None:
    with pytest.raises(UnparseableRecordError):
        parse_record(raw, ingested_at=NOW, known_buses=DEFAULT_TABLES)


def test_group_by_table_and_arrow_schema() -> None:
    records = [
        parse_record(
            body("ingress", eb_event("a", {"x": 1})), ingested_at=NOW, known_buses=DEFAULT_TABLES
        ),
        parse_record(
            body("domain", eb_event("b", {"kind": "event", "type": "T"})),
            ingested_at=NOW,
            known_buses=DEFAULT_TABLES,
        ),
        parse_record(
            body("ingress", eb_event("c", {"x": 2})), ingested_at=NOW, known_buses=DEFAULT_TABLES
        ),
    ]
    groups = group_by_table(records, DEFAULT_TABLES)
    assert {k: len(v) for k, v in groups.items()} == {"ingress_events": 2, "domain_events": 1}
    table = to_arrow(groups["ingress_events"])
    assert table.schema.equals(ARROW_SCHEMA)
    assert table.column("event_id").to_pylist() == ["a", "c"]
    assert table.column("kind").null_count == 2


@pytest.fixture
def catalog(tmp_path: Path) -> SqlCatalog:
    warehouse = tmp_path / "warehouse"
    warehouse.mkdir()
    return SqlCatalog(
        "test", uri=f"sqlite:///{tmp_path}/catalog.db", warehouse=f"file://{warehouse}"
    )


def test_ensure_tables_is_idempotent_and_partitioned_by_day(catalog: SqlCatalog) -> None:
    first = ensure_tables(catalog, "archive", list(DEFAULT_TABLES.values()))
    second = ensure_tables(catalog, "archive", list(DEFAULT_TABLES.values()))
    assert first == second
    table = catalog.load_table(("archive", "ingress_events"))
    assert [f.name for f in table.spec().fields] == ["event_day"]
    assert table.schema().find_field("event_time").required


def test_append_writes_rows_readable_from_latest_snapshot(catalog: SqlCatalog) -> None:
    ensure_tables(catalog, "archive", list(DEFAULT_TABLES.values()))
    records = [
        parse_record(
            body(
                "domain",
                eb_event(
                    f"e{i}", {"kind": "event", "type": "T", "occurredAt": f"2025-09-2{i}T00:00:00Z"}
                ),
            ),
            ingested_at=NOW,
            known_buses=DEFAULT_TABLES,
        )
        for i in range(1, 4)
    ]
    append_with_retry(
        catalog, ("archive", "domain_events"), to_arrow(records), sleep=lambda _: None
    )
    scanned = catalog.load_table(("archive", "domain_events")).scan().to_arrow()
    assert sorted(scanned.column("event_id").to_pylist()) == ["e1", "e2", "e3"]
    assert scanned.schema.field("event_time").type == pa.timestamp("us", tz="UTC")


def test_append_retries_on_commit_conflict(
    catalog: SqlCatalog, monkeypatch: pytest.MonkeyPatch
) -> None:
    ensure_tables(catalog, "archive", ["ingress_events"])
    real_load = catalog.load_table
    attempts: list[int] = []

    class Flaky:
        def __init__(self, table: Any) -> None:
            self.table = table

        def __getattr__(self, name: str) -> Any:  # the catalog reads metadata on commit
            return getattr(self.table, name)

        def append(self, data: pa.Table) -> None:
            attempts.append(1)
            if len(attempts) < 3:
                raise CommitFailedException("conflict")
            self.table.append(data)

    monkeypatch.setattr(catalog, "load_table", lambda ident: Flaky(real_load(ident)))
    rec = parse_record(
        body("ingress", eb_event("z", {"a": 1})), ingested_at=NOW, known_buses=DEFAULT_TABLES
    )
    retries: list[int] = []
    append_with_retry(
        catalog,
        ("archive", "ingress_events"),
        to_arrow([rec]),
        sleep=lambda _: None,
        on_retry=lambda n, _e: retries.append(n),
    )
    assert len(attempts) == 3 and retries == [1, 2]
    assert real_load(("archive", "ingress_events")).scan().to_arrow().num_rows == 1


def test_append_gives_up_after_max_attempts(
    catalog: SqlCatalog, monkeypatch: pytest.MonkeyPatch
) -> None:
    ensure_tables(catalog, "archive", ["ingress_events"])

    class AlwaysConflict:
        def append(self, data: pa.Table) -> None:
            raise CommitFailedException("conflict")

    monkeypatch.setattr(catalog, "load_table", lambda ident: AlwaysConflict())
    rec = parse_record(
        body("ingress", eb_event("z", {"a": 1})), ingested_at=NOW, known_buses=DEFAULT_TABLES
    )
    with pytest.raises(CommitFailedException):
        append_with_retry(
            catalog,
            ("archive", "ingress_events"),
            to_arrow([rec]),
            max_attempts=2,
            sleep=lambda _: None,
        )
