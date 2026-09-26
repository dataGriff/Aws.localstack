"""Both buses' events appear in the Iceberg tables (polled via PyIceberg with a timeout)."""

from __future__ import annotations

import json
from typing import Any

import pytest
from tools.generator import freshen, load_set, send
from tools.query import configure_env_from_outputs

from iceberg import catalog as catalog_config
from tests.helpers.polling import poll_until

pytestmark = pytest.mark.integration
TIMEOUT = 240  # batching window + writer run + commit, with slack


@pytest.fixture(scope="module")
def catalog(outputs: dict[str, str]) -> Any:
    configure_env_from_outputs("local")
    return catalog_config.load()


def _at_least(items: list[dict[str, Any]], n: int) -> list[dict[str, Any]] | None:
    return items if len(items) >= n else None


def _rows(catalog: Any, ns: str, table: str, source_event_id: str) -> list[dict[str, Any]]:
    tbl = catalog.load_table((ns, table))
    arrow = tbl.scan().to_arrow()
    rows = arrow.to_pylist()
    return [r for r in rows if source_event_id in r["detail"]]


def test_ingress_and_domain_events_are_archived(
    outputs: dict[str, str], catalog: Any, run_id: str
) -> None:
    payload = freshen(load_set("valid")[2], f"{run_id}arc")
    send(outputs["IngressBusName"], [payload])
    ns = outputs["IcebergNamespace"]
    third_party_id = payload["id"]

    ingress = poll_until(
        lambda: _rows(catalog, ns, "ingress_events", third_party_id) or None,
        timeout=TIMEOUT,
        interval=5,
        what="ingress row in the archive",
    )
    assert ingress[0]["bus"] == "ingress"
    assert ingress[0]["detail_type"] == "payment.succeeded"
    assert json.loads(ingress[0]["detail"])["id"] == third_party_id

    domain = poll_until(
        lambda: _at_least(_rows(catalog, ns, "domain_events", third_party_id), 2),
        timeout=TIMEOUT,
        interval=5,
        what="PaymentReceived and ReconcileInvoice in the archive",
    )
    by_type = {r["type"]: r for r in domain}
    assert set(by_type) >= {"PaymentReceived", "ReconcileInvoice"}
    assert by_type["PaymentReceived"]["kind"] == "event"
    assert by_type["ReconcileInvoice"]["kind"] == "command"
    assert (
        by_type["PaymentReceived"]["correlation_id"]
        == by_type["ReconcileInvoice"]["correlation_id"]
    )
    # occurredAt (provider time) drives the partition, not the delivery time
    assert by_type["PaymentReceived"]["event_time"].year == 2025


def test_archive_tables_have_the_expected_schema_and_partitioning(
    outputs: dict[str, str], catalog: Any
) -> None:
    ns = outputs["IcebergNamespace"]
    for name in ("ingress_events", "domain_events"):
        table = catalog.load_table((ns, name))
        assert [f.name for f in table.spec().fields] == ["event_day"]
        assert {f.name for f in table.schema().fields} >= {
            "event_id",
            "bus",
            "source",
            "detail_type",
            "kind",
            "type",
            "correlation_id",
            "schema_version",
            "event_time",
            "ingested_at",
            "detail",
        }
