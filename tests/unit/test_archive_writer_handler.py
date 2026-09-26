"""Archive writer Lambda handler with a local catalog and a fake quarantine bucket."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest
from pyiceberg.catalog.sql import SqlCatalog

os.environ.setdefault("ICEBERG_REST_URI", "http://unused")
os.environ.setdefault("ICEBERG_WAREHOUSE", "unused")
os.environ.setdefault("QUARANTINE_BUCKET", "unit-quarantine")

from functions.archive_writer import app
from iceberg.schema import DEFAULT_TABLES
from iceberg.setup import ensure_tables
from tests.unit.test_archive_writer import body, eb_event
from tests.unit.test_translator_handler import Ctx, sqs_event


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[SqlCatalog, dict[str, Any]]:
    (tmp_path / "wh").mkdir()
    catalog = SqlCatalog("t", uri=f"sqlite:///{tmp_path}/c.db", warehouse=f"file://{tmp_path}/wh")
    ensure_tables(catalog, "archive", list(DEFAULT_TABLES.values()))
    quarantined: dict[str, Any] = {}

    class FakeS3:
        def put_object(self, **kw: Any) -> dict[str, Any]:
            quarantined[kw["Key"]] = json.loads(kw["Body"])
            return {}

    monkeypatch.setattr(app, "catalog", lambda: catalog)
    fake_s3 = FakeS3()
    monkeypatch.setattr(app, "s3_client", lambda: fake_s3)
    return catalog, quarantined


def test_batch_is_grouped_appended_and_bad_records_quarantined(
    env: tuple[SqlCatalog, dict[str, Any]],
) -> None:
    catalog, quarantined = env
    event = sqs_event(
        body("ingress", eb_event("i1", {"id": "evt_1"})),
        body("domain", eb_event("d1", {"kind": "event", "type": "PaymentReceived"})),
        "garbage",
        body("ingress", eb_event("i2", {"id": "evt_2"})),
    )
    result = app.handler(event, Ctx())  # type: ignore[arg-type]
    assert result == {"batchItemFailures": []}
    ingress = catalog.load_table(("archive", "ingress_events")).scan().to_arrow()
    domain = catalog.load_table(("archive", "domain_events")).scan().to_arrow()
    assert sorted(ingress.column("event_id").to_pylist()) == ["i1", "i2"]
    assert domain.column("type").to_pylist() == ["PaymentReceived"]
    (key,) = quarantined
    assert key.startswith("archive-writer/unparseable/")


def test_failed_table_append_fails_only_its_records(
    env: tuple[SqlCatalog, dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(catalog: Any, identifier: tuple[str, str], data: Any, **kw: Any) -> None:
        if identifier[1] == "domain_events":
            raise RuntimeError("catalog down")

    monkeypatch.setattr(app, "append_with_retry", broken)
    event = sqs_event(
        body("ingress", eb_event("i1", {"id": "evt_1"})),
        body("domain", eb_event("d1", {"kind": "event", "type": "T"})),
        body("domain", eb_event("d2", {"kind": "command", "type": "C"})),
    )
    result = app.handler(event, Ctx())  # type: ignore[arg-type]
    assert sorted(f["itemIdentifier"] for f in result["batchItemFailures"]) == ["m1", "m2"]
