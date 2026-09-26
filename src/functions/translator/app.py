"""Translator Lambda: ingress bus -> SQS -> validate, deduplicate, map, publish to the domain bus.

Behaviour per SQS record (one EventBridge event from the ingress bus):
1. Parse the EventBridge event; the third-party payload is its `detail`.
2. Identify the contract from `detail-type` (falls back to `detail.type`). Unknown types and
   payloads that violate their contract are written to the quarantine bucket with the reason and
   the record is acknowledged (never failed).
3. Deduplicate on the third-party event id with Powertools idempotency (DynamoDB). A redelivery
   returns the stored result and publishes nothing.
4. Map to domain messages, validate each against its contract, publish with PutEvents (batches of
   10, failed entries retried). Any publish failure fails only that record (partial batch response).
"""

from __future__ import annotations

import importlib
import json
from collections.abc import Callable
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

from domain.registry import MAPPERS, MapperSpec
from shared.config import env, env_int, service_name
from shared.contracts import ContractViolationError, schema_for_envelope, validate
from shared.envelope import Envelope
from shared.eventbridge import publish
from shared.logging import get_logger, get_tracer
from shared.quarantine import quarantine

COMPONENT = "translator"
logger = get_logger(COMPONENT)
tracer = get_tracer(COMPONENT)
processor = BatchProcessor(event_type=EventType.SQS)

persistence = DynamoDBPersistenceLayer(table_name=env("IDEMPOTENCY_TABLE"))
idempotency_config = IdempotencyConfig(
    event_key_jmespath="id",
    raise_on_no_idempotency_key=True,
    expires_after_seconds=env_int("IDEMPOTENCY_TTL_SECONDS", 7 * 24 * 3600),
)

Mapper = Callable[..., list[Envelope]]


@cache
def events_client() -> Any:
    return boto3.client("events")


@cache
def s3_client() -> Any:
    return boto3.client("s3")


@cache
def load_mapper(dotted: str) -> Mapper:
    module_name, _, func_name = dotted.partition(":")
    mapper: Mapper = getattr(importlib.import_module(module_name), func_name)
    return mapper


def resolve_spec(third_party_type: str | None) -> MapperSpec | None:
    return MAPPERS.get(third_party_type or "")


@idempotent_function(
    data_keyword_argument="payload",
    config=idempotency_config,
    persistence_store=persistence,
)
def translate_and_publish(*, payload: dict[str, Any], causation_id: str) -> dict[str, Any]:
    """Map one validated payload to domain messages and publish them. Idempotent on payload id."""
    spec = resolve_spec(payload.get("type"))
    if spec is None:  # pragma: no cover - guarded by the caller
        raise ValueError(f"no mapper for {payload.get('type')}")
    messages = load_mapper(spec.mapper)(payload, causation_id=causation_id)
    for message in messages:
        validate(schema_for_envelope(message.kind, message.type), message.to_detail())

    entries = [
        message.to_eventbridge_entry(env("DOMAIN_BUS_NAME"), service_name()) for message in messages
    ]
    publish(events_client(), entries)
    published = [{"id": m.id, "kind": m.kind, "type": m.type} for m in messages]
    logger.info("published", extra={"published": published, "sourceEventId": payload.get("id")})
    return {"published": published}


def _quarantine(reason_code: str, reason: str, record_id: str, payload: Any) -> None:
    key = quarantine(
        s3_client(),
        env("QUARANTINE_BUCKET"),
        component=COMPONENT,
        reason_code=reason_code,
        reason=reason,
        record_id=record_id,
        payload=payload,
    )
    logger.warning("quarantined", extra={"reasonCode": reason_code, "reason": reason, "key": key})


@tracer.capture_method
def record_handler(record: SQSRecord) -> None:
    try:
        event = json.loads(record.body)
    except json.JSONDecodeError as exc:
        _quarantine(
            "malformed-body", f"SQS body is not JSON: {exc}", record.message_id, record.body
        )
        return
    if not isinstance(event, dict) or not isinstance(event.get("detail"), dict):
        _quarantine(
            "malformed-event",
            "not an EventBridge event with an object detail",
            record.message_id,
            event,
        )
        return

    payload: dict[str, Any] = event["detail"]
    ingress_id = str(event.get("id") or record.message_id)
    record_id = str(payload.get("id") or ingress_id)
    logger.append_keys(sourceEventId=record_id, ingressEventId=ingress_id)

    third_party_type = event.get("detail-type") or payload.get("type")
    spec = resolve_spec(third_party_type)
    if spec is None:
        _quarantine(
            "unknown-type", f"no mapper registered for {third_party_type!r}", record_id, payload
        )
        return
    try:
        validate(spec.schema, payload)
    except ContractViolationError as exc:
        _quarantine("schema", str(exc), record_id, payload)
        return

    translate_and_publish(payload=payload, causation_id=ingress_id)


@logger.inject_lambda_context(log_event=False)
@tracer.capture_lambda_handler
def handler(event: dict[str, Any], context: LambdaContext) -> PartialItemFailureResponse:
    return process_partial_response(
        event=event, record_handler=record_handler, processor=processor, context=context
    )
