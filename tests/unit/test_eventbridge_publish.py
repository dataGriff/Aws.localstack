from __future__ import annotations

from typing import Any

import pytest

from shared.eventbridge import PublishError, chunked, publish


class FakeEvents:
    def __init__(self, fail_plan: list[set[int]]) -> None:
        # fail_plan[i] = indexes (within the call's Entries) to fail on call i
        self.fail_plan = fail_plan
        self.calls: list[list[dict[str, Any]]] = []

    def put_events(self, *, Entries: Any) -> dict[str, Any]:  # noqa: N803
        entries = list(Entries)
        self.calls.append(entries)
        call_no = len(self.calls) - 1
        failing = self.fail_plan[call_no] if call_no < len(self.fail_plan) else set()
        results = [
            {"ErrorCode": "InternalFailure", "ErrorMessage": "boom"}
            if i in failing
            else {"EventId": f"e{i}"}
            for i in range(len(entries))
        ]
        return {"FailedEntryCount": len(failing), "Entries": results}


def entries(n: int) -> list[dict[str, Any]]:
    return [{"Detail": str(i)} for i in range(n)]


def test_chunks_of_ten() -> None:
    assert [len(c) for c in chunked(entries(23))] == [10, 10, 3]


def test_all_succeed_single_call_per_chunk() -> None:
    client = FakeEvents([])
    publish(client, entries(12), sleep=lambda _: None)
    assert [len(c) for c in client.calls] == [10, 2]


def test_only_failed_entries_are_retried() -> None:
    client = FakeEvents([{1, 3}, set()])
    publish(client, entries(5), sleep=lambda _: None)
    assert len(client.calls) == 2
    assert [e["Detail"] for e in client.calls[1]] == ["1", "3"]


def test_gives_up_after_max_attempts() -> None:
    client = FakeEvents([{0}] * 10)
    with pytest.raises(PublishError) as excinfo:
        publish(client, entries(1), max_attempts=3, sleep=lambda _: None)
    assert len(client.calls) == 3
    assert excinfo.value.failed == [{"Detail": "0"}]
