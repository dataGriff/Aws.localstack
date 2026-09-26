"""ReconcileInvoice: decide what to record for an invoice/payment pair."""

from __future__ import annotations

from typing import Any

from shared.envelope import Envelope


def build_reconciliation(command: Envelope, *, reconciled_at: str) -> dict[str, Any]:
    """Return the DynamoDB item recording that the invoice was reconciled.

    The item key is the invoice id so repeated commands for one invoice converge on one row;
    the command id is kept for traceability.
    """
    data = command.data
    return {
        "pk": f"INVOICE#{data['invoiceId']}",
        "sk": f"PAYMENT#{data['paymentId']}",
        "invoiceId": data["invoiceId"],
        "paymentId": data["paymentId"],
        "amount": int(data["amount"]),
        "currency": data["currency"],
        "status": "reconciled",
        "commandId": command.id,
        "correlationId": command.correlation_id,
        "sourceEventId": command.source,
        "synthetic": command.synthetic,
        "reconciledAt": reconciled_at,
    }
