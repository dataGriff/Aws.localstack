"""reconcile-invoice handler with a fake DynamoDB table."""

from __future__ import annotations

import json
import os
from typing import Any

import pytest

os.environ.setdefault("IDEMPOTENCY_TABLE", "unit-idempotency")
os.environ.setdefault("RESULT_TABLE", "unit-results")

from functions.reconcile_invoice import app
from shared.envelope import Envelope
from tests.unit.test_translator_handler import Ctx, sqs_event


class FakeTable:
    def __init__(self) -> None:
        self.items: list[dict[str, Any]] = []

    def put_item(self, *, Item: dict[str, Any]) -> dict[str, Any]:  # noqa: N803
        self.items.append(Item)
        return {}


def command_event(**overrides: Any) -> dict[str, Any]:
    env = Envelope(
        kind="command",
        type="ReconcileInvoice",
        occurred_at="2025-09-26T00:00:00.000Z",
        correlation_id="corr",
        causation_id="cause",
        source="evt_1",
        data={"invoiceId": "inv_1", "paymentId": "pay_1", "amount": 100, "currency": "GBP"},
    )
    detail = env.to_detail() | overrides
    return {
        "version": "0",
        "id": "eb-1",
        "source": "event-platform.translator",
        "detail-type": detail["type"],
        "time": "2025-09-26T00:00:00Z",
        "detail": detail,
    }


@pytest.fixture
def table(monkeypatch: pytest.MonkeyPatch) -> FakeTable:
    fake = FakeTable()
    monkeypatch.setattr(app, "result_table", lambda: fake)
    return fake


def test_command_is_recorded(table: FakeTable) -> None:
    result = app.handler(sqs_event(command_event()), Ctx())  # type: ignore[arg-type]
    assert result == {"batchItemFailures": []}
    assert table.items[0]["pk"] == "INVOICE#inv_1"
    assert table.items[0]["status"] == "reconciled"


def test_poison_and_misrouted_messages_fail_their_record_only(table: FakeTable) -> None:
    event = sqs_event(
        "not json",
        command_event(type="PaymentReceived", kind="event"),
        json.dumps(command_event(data={"invoiceId": "inv_1"})),
        command_event(),
    )
    result = app.handler(event, Ctx())  # type: ignore[arg-type]
    failed = sorted(f["itemIdentifier"] for f in result["batchItemFailures"])
    assert failed == ["m0", "m1", "m2"]
    assert len(table.items) == 1
