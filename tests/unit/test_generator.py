from __future__ import annotations

import json
from typing import Any

from tools.generator import ALL_SETS, freshen, load_set, mark_synthetic, send, to_entry

from shared.contracts import validate


def test_freshen_suffixes_provider_ids_consistently() -> None:
    a, b = (freshen(p, "abc") for p in load_set("duplicates")[:2])
    assert a == b  # duplicates still collide within one run
    assert a["id"].endswith("_abc")
    assert a["data"]["object"]["invoice"].endswith("_abc")
    validate("thirdparty/payment.succeeded.schema.json", a)


def test_synthetic_marker_is_visible_in_id_and_payload() -> None:
    p = mark_synthetic(load_set("valid")[0])
    assert p["synthetic"] is True
    assert p["id"].startswith("evt_synthetic_")
    validate("thirdparty/payment.succeeded.schema.json", p)


def test_entries_keep_payload_unchanged_and_route_by_provider_type() -> None:
    payload = load_set("valid")[0]
    entry = to_entry("bus", payload)
    assert entry["Source"] == "event-platform.thirdparty.payments"
    assert entry["DetailType"] == "payment.succeeded"
    assert json.loads(entry["Detail"]) == payload


def test_send_batches_every_set() -> None:
    class Fake:
        calls: list[int] = []

        def put_events(self, *, Entries: Any) -> dict[str, Any]:  # noqa: N803
            self.calls.append(len(Entries))
            return {"FailedEntryCount": 0, "Entries": [{"EventId": "x"} for _ in Entries]}

    fake = Fake()
    payloads = [p for s in ALL_SETS for p in load_set(s)]
    assert send("bus", payloads, client=fake) == len(payloads)
    assert max(fake.calls) <= 10
