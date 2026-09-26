"""generator -> ingress bus -> translator -> domain bus (observed via a temporary tap)."""

from __future__ import annotations

import json
from typing import Any

import pytest
from tools.generator import freshen, load_set, send

from shared.contracts import schema_for_envelope, validate
from tests.helpers.polling import poll_until, stays_unchanged
from tests.integration.conftest import DomainMessages

pytestmark = pytest.mark.integration
TIMEOUT = 90


def _at_least(items: list[dict[str, Any]], n: int) -> list[dict[str, Any]] | None:
    return items if len(items) >= n else None


def test_valid_event_reaches_domain_bus_as_event_and_command(
    outputs: dict[str, str], domain_messages: DomainMessages, run_id: str
) -> None:
    payload = freshen(load_set("valid")[0], run_id)
    send(outputs["IngressBusName"], [payload])

    msgs = poll_until(
        lambda: _at_least(domain_messages.for_source(payload["id"]), 2),
        timeout=TIMEOUT,
        what="PaymentReceived and ReconcileInvoice on the domain bus",
    )
    by_type = {m["detail-type"]: m for m in msgs}
    assert set(by_type) == {"PaymentReceived", "ReconcileInvoice"}
    for msg in msgs:
        detail = msg["detail"]
        validate(schema_for_envelope(detail["kind"], detail["type"]), detail)
        assert msg["source"].endswith(".translator")
        assert msg["detail-type"] == detail["type"]
    event, command = by_type["PaymentReceived"]["detail"], by_type["ReconcileInvoice"]["detail"]
    assert command["causationId"] == event["id"]
    assert command["correlationId"] == event["correlationId"]
    assert event["data"]["invoiceId"] == payload["data"]["object"]["invoice"]


def test_duplicates_produce_exactly_one_event_and_one_command(
    outputs: dict[str, str], domain_messages: DomainMessages, run_id: str
) -> None:
    payloads = [freshen(p, run_id) for p in load_set("duplicates")]
    assert len({p["id"] for p in payloads}) == 1
    send(outputs["IngressBusName"], payloads)
    source_id = payloads[0]["id"]

    poll_until(
        lambda: len(domain_messages.for_source(source_id)) >= 2 or None,
        timeout=TIMEOUT,
        what="first translation of the duplicated event",
    )
    count = stays_unchanged(
        lambda: len(domain_messages.for_source(source_id)), quiet=15, what="domain message count"
    )
    assert count == 2
    kinds = sorted(m["detail"]["kind"] for m in domain_messages.for_source(source_id))
    assert kinds == ["command", "event"]


def test_out_of_order_events_are_translated_independently(
    outputs: dict[str, str], domain_messages: DomainMessages, run_id: str
) -> None:
    payloads = [freshen(p, run_id) for p in load_set("out_of_order")]
    send(outputs["IngressBusName"], payloads)
    for payload in payloads:
        msgs = poll_until(
            lambda p=payload: _at_least(domain_messages.for_source(p["id"]), 2),
            timeout=TIMEOUT,
            what=f"translation of {payload['id']}",
        )
        event = next(m["detail"] for m in msgs if m["detail"]["kind"] == "event")
        assert event["occurredAt"].startswith("2025-09-26T")  # provider time, not delivery time


def _quarantined_keys(s3: Any, bucket: str, prefix: str) -> list[str]:
    resp = s3.list_objects_v2(Bucket=bucket, Prefix=prefix)
    return [o["Key"] for o in resp.get("Contents", [])]


def test_invalid_payloads_land_in_quarantine_with_reason(
    outputs: dict[str, str], s3_client: Any, run_id: str
) -> None:
    payloads = [freshen(p, run_id) for p in load_set("invalid")]
    send(outputs["IngressBusName"], payloads)
    bucket = outputs["QuarantineBucketName"]
    ids = [p["id"] for p in payloads if "id" in p]

    def probe() -> list[str] | None:
        keys = _quarantined_keys(s3_client, bucket, "translator/")
        found = [k for k in keys if any(i in k for i in ids)]
        unknown = [k for k in keys if k.startswith("translator/unknown-type/")]
        return keys if len(found) >= len(ids) and unknown else None

    keys = poll_until(probe, timeout=TIMEOUT, what="quarantined objects")
    schema_keys = [k for k in keys if k.startswith("translator/schema/") and ids[0] in k]
    body = json.loads(s3_client.get_object(Bucket=bucket, Key=schema_keys[0])["Body"].read())
    assert body["reasonCode"] == "schema"
    assert "created" in body["reason"]
    assert body["payload"]["id"] == ids[0]
