from __future__ import annotations

from domain.mappers.payment_succeeded import map_payment_succeeded
from shared.contracts import schema_for_envelope, validate
from tests.helpers.fixtures import first_valid_payload


def test_maps_to_event_then_command_and_both_satisfy_contracts() -> None:
    payload = first_valid_payload()
    messages = map_payment_succeeded(payload, causation_id="ingress-event-id")
    assert [m.kind for m in messages] == ["event", "command"]
    event, command = messages
    for m in messages:
        validate(schema_for_envelope(m.kind, m.type), m.to_detail())

    assert event.type == "PaymentReceived"
    assert event.causation_id == "ingress-event-id"
    assert event.source == payload["id"]
    assert event.data["currency"] == payload["data"]["object"]["currency"].upper()
    assert event.occurred_at.startswith("2025-09-26T")

    assert command.type == "ReconcileInvoice"
    assert command.causation_id == event.id
    assert command.correlation_id == event.correlation_id
    assert command.data["invoiceId"] == payload["data"]["object"]["invoice"]


def test_synthetic_marker_propagates() -> None:
    payload = first_valid_payload() | {"synthetic": True}
    assert all(m.synthetic for m in map_payment_succeeded(payload, causation_id="x"))
