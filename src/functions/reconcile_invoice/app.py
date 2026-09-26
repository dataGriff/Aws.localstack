"""reconcile-invoice command handler: SQS -> validate envelope -> idempotent on id -> DynamoDB.

Each SQS record carries one EventBridge event from the domain bus whose `detail` is a
ReconcileInvoice envelope. Records that are not valid ReconcileInvoice commands are a routing
or contract bug, so they are *failed* (and end up in the DLQ after maxReceiveCount), not
silently dropped. Executions are idempotent on the envelope id.
"""

from __future__ import annotations

import json
from functools import cache
from typing import Any

import boto3
from aws_lambda_powertools.utilities.batch import (
    BatchProcessor,
    EventType,
    process_partial_response,
)
from aws_lambda_powertools.utilities.batch.types import PartialItemFailureResponse
from aws_lambda_powertools.utilities.data_classes.sqs_event import SQSRecord
from aws_lambda_powertools.utilities.idempotency import (
    DynamoDBPersistenceLayer,
    IdempotencyConfig,
    idempotent_function,
)
from aws_lambda_powertools.utilities.typing import LambdaContext

from domain.commands.reconcile_invoice import build_reconciliation
from shared.config import env, env_int
from shared.contracts import schema_for_envelope, validate
from shared.envelope import Envelope, now_iso
from shared.logging import get_logger, get_tracer

COMPONENT = "reconcile-invoice"
COMMAND_TYPE = "ReconcileInvoice"
logger = get_logger(COMPONENT)
tracer = get_tracer(COMPONENT)
processor = BatchProcessor(event_type=EventType.SQS)

persistence = DynamoDBPersistenceLayer(table_name=env("IDEMPOTENCY_TABLE"))
idempotency_config = IdempotencyConfig(
    event_key_jmespath="id",
    raise_on_no_idempotency_key=True,
    expires_after_seconds=env_int("IDEMPOTENCY_TTL_SECONDS", 7 * 24 * 3600),
)


@cache
def result_table() -> Any:
    return boto3.resource("dynamodb").Table(env("RESULT_TABLE"))


@idempotent_function(
    data_keyword_argument="detail", config=idempotency_config, persistence_store=persistence
)
def execute(*, detail: dict[str, Any]) -> dict[str, Any]:
    """Carry out one ReconcileInvoice command. Idempotent on the envelope id."""
    command = Envelope.from_detail(detail)
    item = build_reconciliation(command, reconciled_at=now_iso())
    result_table().put_item(Item=item)
    logger.info("reconciled", extra={"invoiceId": item["invoiceId"], "commandId": command.id})
    return {"pk": item["pk"], "sk": item["sk"]}


@tracer.capture_method
def record_handler(record: SQSRecord) -> None:
    event = json.loads(record.body)
    if not isinstance(event, dict) or not isinstance(event.get("detail"), dict):
        raise ValueError("SQS body is not an EventBridge event with an object detail")
    detail: dict[str, Any] = event["detail"]
    if detail.get("kind") != "command" or detail.get("type") != COMMAND_TYPE:
        raise ValueError(f"misrouted message: kind={detail.get('kind')} type={detail.get('type')}")
    validate(schema_for_envelope("command", COMMAND_TYPE), detail)
    logger.set_correlation_id(detail["correlationId"])
    execute(detail=detail)


@logger.inject_lambda_context(log_event=False)
@tracer.capture_lambda_handler
def handler(event: dict[str, Any], context: LambdaContext) -> PartialItemFailureResponse:
    return process_partial_response(
        event=event, record_handler=record_handler, processor=processor, context=context
    )
