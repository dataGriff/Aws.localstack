"""Archive writer Lambda (container image): shared archive SQS queue -> Iceberg tables.

Batches are large (size and window are configurable). Per invocation:
  parse every record (unparseable -> quarantine, acknowledged) ->
  group by target table (one table per bus) -> one Arrow table per group ->
  one append() per table, retried on commit conflicts ->
  records whose table append ultimately failed are reported as batch item failures.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from functools import cache
from typing import Any

import boto3
from aws_lambda_powertools.utilities.batch.types import (
    PartialItemFailureResponse,
    PartialItemFailures,
)
from aws_lambda_powertools.utilities.typing import LambdaContext

from functions.archive_writer.writer import (
    ArchiveRecord,
    UnparseableRecordError,
    append_with_retry,
    group_by_table,
    parse_record,
    to_arrow,
)
from iceberg import catalog as catalog_config
from shared.config import env
from shared.logging import get_logger, get_tracer
from shared.quarantine import quarantine

COMPONENT = "archive-writer"
logger = get_logger(COMPONENT)
tracer = get_tracer(COMPONENT)


@cache
def catalog() -> Any:
    return catalog_config.load()


@cache
def s3_client() -> Any:
    return boto3.client("s3")


def _quarantine(record: dict[str, Any], reason: str) -> None:
    key = quarantine(
        s3_client(),
        env("QUARANTINE_BUCKET"),
        component=COMPONENT,
        reason_code="unparseable",
        reason=reason,
        record_id=record["messageId"],
        payload=record.get("body"),
    )
    logger.warning("quarantined", extra={"reason": reason, "key": key})


def _retry_logger(table_name: str) -> Callable[[int, Exception], None]:
    def log(attempt: int, exc: Exception) -> None:
        logger.warning(
            "commit conflict, retrying",
            extra={"table": table_name, "attempt": attempt, "error": str(exc)},
        )

    return log


@logger.inject_lambda_context(log_event=False)
@tracer.capture_lambda_handler
def handler(event: dict[str, Any], context: LambdaContext) -> PartialItemFailureResponse:
    tables = catalog_config.table_names()
    ns = catalog_config.namespace()
    ingested_at = datetime.now(tz=UTC)

    parsed: list[tuple[str, ArchiveRecord]] = []
    for record in event.get("Records", []):
        try:
            parsed.append(
                (
                    record["messageId"],
                    parse_record(record["body"], ingested_at=ingested_at, known_buses=tables),
                )
            )
        except UnparseableRecordError as exc:
            _quarantine(record, str(exc))

    failures: list[PartialItemFailures] = []
    groups = group_by_table((r for _, r in parsed), tables)
    for table_name, records in groups.items():
        data = to_arrow(records)
        try:
            append_with_retry(
                catalog(),
                (ns, table_name),
                data,
                on_retry=_retry_logger(table_name),
            )
            logger.info("appended", extra={"table": table_name, "rows": data.num_rows})
        except Exception as exc:
            logger.exception("append failed", extra={"table": table_name, "error": str(exc)})
            ids = {r.event_id for r in records}
            failures.extend(
                {"itemIdentifier": message_id} for message_id, r in parsed if r.event_id in ids
            )
    logger.info(
        "batch done",
        extra={
            "records": len(event.get("Records", [])),
            "parsed": len(parsed),
            "failed": len(failures),
        },
    )
    return {"batchItemFailures": failures}


def _dump(obj: Any) -> str:  # pragma: no cover - debugging aid
    return json.dumps(obj, default=str)
