from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from shared.quarantine import quarantine, quarantine_key


class FakeS3:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def put_object(self, **kwargs: Any) -> dict[str, Any]:
        self.objects[kwargs["Key"]] = kwargs["Body"]
        return {}


def test_key_layout_and_sanitisation() -> None:
    key = quarantine_key("translator", "schema", "evt/../x y", datetime(2025, 9, 26, tzinfo=UTC))
    assert key == "translator/schema/2025/09/26/evt_.._x_y.json"


def test_writes_reason_and_payload() -> None:
    s3 = FakeS3()
    key = quarantine(
        s3,
        "bucket",
        component="translator",
        reason_code="schema",
        reason="missing created",
        record_id="evt_1",
        payload={"id": "evt_1"},
    )
    body = json.loads(s3.objects[key])
    assert body["reason"] == "missing created"
    assert body["payload"] == {"id": "evt_1"}
    assert body["reasonCode"] == "schema"
