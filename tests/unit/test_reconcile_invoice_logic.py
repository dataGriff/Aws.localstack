from __future__ import annotations

from domain.commands.reconcile_invoice import build_reconciliation
from shared.envelope import Envelope


def test_item_is_keyed_by_invoice_and_payment() -> None:
    cmd = Envelope(
        kind="command",
        type="ReconcileInvoice",
        occurred_at="2025-09-26T00:00:00Z",
        correlation_id="c",
        causation_id="e",
        source="evt_1",
        data={"invoiceId": "inv_1", "paymentId": "pay_1", "amount": 5, "currency": "GBP"},
    )
    item = build_reconciliation(cmd, reconciled_at="2025-09-26T00:00:01Z")
    assert item["pk"] == "INVOICE#inv_1"
    assert item["sk"] == "PAYMENT#pay_1"
    assert item["status"] == "reconciled"
    assert item["commandId"] == cmd.id
