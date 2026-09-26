"""Maps the provider's payment.succeeded webhook to PaymentReceived + ReconcileInvoice."""

from __future__ import annotations

from typing import Any

from shared.envelope import Envelope, epoch_to_iso, new_id

PROVIDER = "payments"


def map_payment_succeeded(payload: dict[str, Any], *, causation_id: str) -> list[Envelope]:
    """Return the domain messages derived from one validated payment.succeeded payload.

    `causation_id` is the id of the ingress EventBridge event that carried the payload.
    Ordering matters: the event is published first, then the command caused by it.
    """
    obj = payload["data"]["object"]
    occurred_at = epoch_to_iso(int(payload["created"]))
    correlation_id = new_id()
    synthetic = bool(payload.get("synthetic", False))

    event = Envelope(
        kind="event",
        type="PaymentReceived",
        occurred_at=occurred_at,
        correlation_id=correlation_id,
        causation_id=causation_id,
        source=payload["id"],
        synthetic=synthetic,
        data={
            "paymentId": obj["id"],
            "invoiceId": obj["invoice"],
            "customerId": obj["customer"],
            "amount": int(obj["amount"]),
            "currency": str(obj["currency"]).upper(),
            "receivedAt": occurred_at,
            "provider": PROVIDER,
        },
    )
    command = Envelope(
        kind="command",
        type="ReconcileInvoice",
        occurred_at=occurred_at,
        correlation_id=correlation_id,
        causation_id=event.id,
        source=payload["id"],
        synthetic=synthetic,
        data={
            "invoiceId": obj["invoice"],
            "paymentId": obj["id"],
            "amount": int(obj["amount"]),
            "currency": str(obj["currency"]).upper(),
        },
    )
    return [event, command]
