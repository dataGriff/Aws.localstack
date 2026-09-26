"""Translator handler with fake AWS clients (idempotency and tracing disabled via conftest)."""

from __future__ import annotations

import json
import os
from typing import Any

import pytest

os.environ.setdefault("IDEMPOTENCY_TABLE", "unit-idempotency")
os.environ.setdefault("DOMAIN_BUS_NAME", "unit-domain-bus")
os.environ.setdefault("QUARANTINE_BUCKET", "unit-quarantine")

from functions.translator import app
from tests.helpers.fixtures import first_valid_payload


class FakeEvents:
    def __init__(self) -> None:
        self.entries: list[dict[str, Any]] = []

    def put_events(self, *, Entries: Any) -> dict[str, Any]:  # noqa: N803
        self.entries.extend(Entries)
        return {"FailedEntryCount": 0, "Entries": [{"EventId": "x"} for _ in Entries]}


class FakeS3:
    def __init__(self) -> None:
        self.objects: dict[str, dict[str, Any]] = {}

    def put_object(self, **kwargs: Any) -> dict[str, Any]:
        self.objects[kwargs["Key"]] = json.loads(kwargs["Body"])
        return {}


class Ctx:
    function_name = "translator"
    memory_limit_in_mb = 128
    invoked_function_arn = "arn:aws:lambda:eu-west-1:000000000000:function:translator"
    aws_request_id = "req-1"

    def get_remaining_time_in_millis(self) -> int:
        return 10_000


def sqs_event(*bodies: Any) -> dict[str, Any]:
    return {
        "Records": [
            {
                "messageId": f"m{i}",
                "receiptHandle": "r",
                "attributes": {},
                "messageAttributes": {},
                "md5OfBody": "",
                "eventSource": "aws:sqs",
                "awsRegion": "eu-west-1",
                "eventSourceARN": "arn:aws:sqs:eu-west-1:000000000000:q",
                "body": body if isinstance(body, str) else json.dumps(body),
            }
            for i, body in enumerate(bodies)
        ]
    }


def eb_event(
    detail: Any, detail_type: str | None = None, event_id: str = "ingress-1"
) -> dict[str, Any]:
    return {
        "version": "0",
        "id": event_id,
        "account": "000000000000",
        "region": "eu-west-1",
        "time": "2025-09-26T00:00:00Z",
        "resources": [],
        "source": "event-platform.thirdparty.payments",
        "detail-type": detail_type
        or (detail.get("type") if isinstance(detail, dict) else "unknown"),
        "detail": detail,
    }


@pytest.fixture
def clients(monkeypatch: pytest.MonkeyPatch) -> tuple[FakeEvents, FakeS3]:
    events, s3 = FakeEvents(), FakeS3()
    monkeypatch.setattr(app, "events_client", lambda: events)
    monkeypatch.setattr(app, "s3_client", lambda: s3)
    return events, s3


def test_valid_payload_is_published_as_event_and_command(
    clients: tuple[FakeEvents, FakeS3],
) -> None:
    events, s3 = clients
    result = app.handler(sqs_event(eb_event(first_valid_payload())), Ctx())  # type: ignore[arg-type]
    assert result == {"batchItemFailures": []}
    assert [e["DetailType"] for e in events.entries] == ["PaymentReceived", "ReconcileInvoice"]
    assert all(e["EventBusName"] == "unit-domain-bus" for e in events.entries)
    assert json.loads(events.entries[0]["Detail"])["causationId"] == "ingress-1"
    assert not s3.objects


def test_invalid_payload_is_quarantined_not_failed(clients: tuple[FakeEvents, FakeS3]) -> None:
    events, s3 = clients
    bad = first_valid_payload()
    del bad["created"]
    result = app.handler(sqs_event(eb_event(bad)), Ctx())  # type: ignore[arg-type]
    assert result == {"batchItemFailures": []}
    assert not events.entries
    ((key, body),) = s3.objects.items()
    assert key.startswith("translator/schema/")
    assert "created" in body["reason"]


def test_unknown_type_and_malformed_bodies_are_quarantined(
    clients: tuple[FakeEvents, FakeS3],
) -> None:
    events, s3 = clients
    result = app.handler(
        sqs_event(eb_event({"hello": "world"}, "unknown"), "not json", eb_event("str-detail")),
        Ctx(),  # type: ignore[arg-type]
    )
    assert result == {"batchItemFailures": []}
    assert not events.entries
    codes = sorted(k.split("/")[1] for k in s3.objects)
    assert codes == ["malformed-body", "malformed-event", "unknown-type"]


def test_publish_failure_fails_only_that_record(
    clients: tuple[FakeEvents, FakeS3], monkeypatch: pytest.MonkeyPatch
) -> None:
    class Broken(FakeEvents):
        def put_events(self, *, Entries: Any) -> dict[str, Any]:  # noqa: N803
            return {
                "FailedEntryCount": len(Entries),
                "Entries": [{"ErrorCode": "InternalFailure"} for _ in Entries],
            }

    broken = Broken()
    monkeypatch.setattr(app, "events_client", lambda: broken)
    monkeypatch.setattr("shared.eventbridge.time.sleep", lambda _: None)
    good_then_bad = sqs_event(
        eb_event({"hello": "world"}, "unknown"), eb_event(first_valid_payload())
    )
    result = app.handler(good_then_bad, Ctx())  # type: ignore[arg-type]
    assert result == {"batchItemFailures": [{"itemIdentifier": "m1"}]}
