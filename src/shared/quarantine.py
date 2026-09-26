"""Writes rejected records to an S3 quarantine prefix with the reason alongside."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any, Protocol


class S3Client(Protocol):
    def put_object(self, **kwargs: Any) -> dict[str, Any]: ...


def quarantine_key(
    component: str, reason_code: str, record_id: str, when: datetime | None = None
) -> str:
    when = when or datetime.now(tz=UTC)
    safe_id = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in record_id)[:128]
    return f"{component}/{reason_code}/{when:%Y/%m/%d}/{safe_id}.json"


def quarantine(
    client: S3Client,
    bucket: str,
    *,
    component: str,
    reason_code: str,
    reason: str,
    record_id: str,
    payload: Any,
) -> str:
    key = quarantine_key(component, reason_code, record_id)
    body = {
        "quarantinedAt": datetime.now(tz=UTC).isoformat(),
        "component": component,
        "reasonCode": reason_code,
        "reason": reason,
        "recordId": record_id,
        "payload": payload,
    }
    client.put_object(
        Bucket=bucket,
        Key=key,
        Body=json.dumps(body, default=str).encode("utf-8"),
        ContentType="application/json",
    )
    return key
