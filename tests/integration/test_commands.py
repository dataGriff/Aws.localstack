"""domain bus -> command queue -> handler -> DynamoDB; duplicates once; poison -> DLQ."""

from __future__ import annotations

import json
from typing import Any

import pytest
from tools.generator import freshen, load_set, send

from tests.helpers.polling import poll_until, stays_unchanged

pytestmark = pytest.mark.integration
TIMEOUT = 120


def _reconciliations(dynamodb: Any, table_name: str, invoice_id: str) -> list[dict[str, Any]]:
    table = dynamodb.Table(table_name)
    resp = table.query(
        KeyConditionExpression="pk = :pk",
        ExpressionAttributeValues={":pk": f"INVOICE#{invoice_id}"},
    )
    return list(resp.get("Items", []))


def test_end_to_end_reconciliation_result_present(
    outputs: dict[str, str], dynamodb: Any, run_id: str
) -> None:
    payload = freshen(load_set("valid")[1], run_id)
    send(outputs["IngressBusName"], [payload])
    invoice = payload["data"]["object"]["invoice"]

    items = poll_until(
        lambda: _reconciliations(dynamodb, outputs["ReconcileInvoiceTableName"], invoice) or None,
        timeout=TIMEOUT,
        what=f"reconciliation row for {invoice}",
    )
    (item,) = items
    assert item["paymentId"] == payload["data"]["object"]["id"]
    assert int(item["amount"]) == payload["data"]["object"]["amount"]
    assert item["currency"] == payload["data"]["object"]["currency"].upper()
    assert item["sourceEventId"] == payload["id"]
    assert item["status"] == "reconciled"


def test_duplicate_deliveries_execute_the_command_once(
    outputs: dict[str, str], dynamodb: Any, run_id: str
) -> None:
    payloads = [freshen(p, f"{run_id}cmd") for p in load_set("duplicates")]
    send(outputs["IngressBusName"], payloads)
    invoice = payloads[0]["data"]["object"]["invoice"]
    table = outputs["ReconcileInvoiceTableName"]

    poll_until(
        lambda: _reconciliations(dynamodb, table, invoice) or None,
        timeout=TIMEOUT,
        what="first reconciliation",
    )
    items = stays_unchanged(
        lambda: _reconciliations(dynamodb, table, invoice), quiet=15, what="reconciliation rows"
    )
    assert len(items) == 1
    assert len({i["commandId"] for i in items}) == 1


def test_poisoned_command_message_ends_up_in_its_dlq(
    outputs: dict[str, str], sqs_client: Any, run_id: str
) -> None:
    marker = f"poison-{run_id}"
    sqs_client.send_message(
        QueueUrl=outputs["ReconcileInvoiceQueueUrl"],
        MessageBody=json.dumps({"detail": {"kind": "command", "type": "Nope", "marker": marker}}),
    )

    def in_dlq() -> dict[str, Any] | None:
        resp = sqs_client.receive_message(
            QueueUrl=outputs["ReconcileInvoiceDlqUrl"],
            MaxNumberOfMessages=10,
            WaitTimeSeconds=2,
            VisibilityTimeout=5,
        )
        for m in resp.get("Messages", []):
            if marker in m["Body"]:
                sqs_client.delete_message(
                    QueueUrl=outputs["ReconcileInvoiceDlqUrl"], ReceiptHandle=m["ReceiptHandle"]
                )
                return m
        return None

    # maxReceiveCount * visibility timeout (local: 3 * 30s) plus slack.
    poll_until(in_dlq, timeout=240, interval=3, what="poison message in the handler DLQ")
