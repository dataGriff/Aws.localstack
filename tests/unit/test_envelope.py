from __future__ import annotations

import json
import uuid

from shared.contracts import validate
from shared.envelope import Envelope, epoch_to_iso


def make(**overrides: object) -> Envelope:
    base: dict[str, object] = {
        "kind": "event",
        "type": "PaymentReceived",
        "occurred_at": "2025-09-26T00:00:00.000Z",
        "correlation_id": "corr-1",
        "causation_id": "cause-1",
        "source": "evt_1",
        "data": {
            "paymentId": "pay_1",
            "invoiceId": "inv_1",
            "customerId": "cus_1",
            "amount": 100,
            "currency": "GBP",
            "receivedAt": "2025-09-26T00:00:00.000Z",
            "provider": "payments",
        },
    }
    base.update(overrides)
    return Envelope(**base)  # type: ignore[arg-type]


def test_to_detail_matches_envelope_contract() -> None:
    detail = make().to_detail()
    validate("envelope.schema.json", detail)
    uuid.UUID(detail["id"])
    assert "synthetic" not in detail


def test_synthetic_flag_is_serialised_only_when_true() -> None:
    assert make(synthetic=True).to_detail()["synthetic"] is True


def test_round_trip() -> None:
    env = make(synthetic=True)
    assert Envelope.from_detail(env.to_detail()) == env


def test_eventbridge_entry_routes_on_type_and_stable_source() -> None:
    entry = make().to_eventbridge_entry("domain-bus", "event-platform")
    assert entry["DetailType"] == "PaymentReceived"
    assert entry["Source"] == "event-platform.translator"
    assert entry["EventBusName"] == "domain-bus"
    assert json.loads(entry["Detail"])["type"] == "PaymentReceived"


def test_epoch_to_iso() -> None:
    assert epoch_to_iso(0) == "1970-01-01T00:00:00.000Z"
