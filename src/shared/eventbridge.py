"""PutEvents with batching (max 10 entries per call), failure detection and retry."""

from __future__ import annotations

import random
import time
from collections.abc import Callable, Iterable, Sequence
from typing import Any, Protocol

MAX_ENTRIES_PER_CALL = 10


class EventsClient(Protocol):
    def put_events(self, *, Entries: Sequence[dict[str, Any]]) -> dict[str, Any]: ...  # noqa: N803


class PublishError(RuntimeError):
    def __init__(self, failed: list[dict[str, Any]]) -> None:
        self.failed = failed
        super().__init__(f"{len(failed)} EventBridge entries failed after retries")


def chunked(
    items: Sequence[dict[str, Any]], size: int = MAX_ENTRIES_PER_CALL
) -> Iterable[list[dict[str, Any]]]:
    for start in range(0, len(items), size):
        yield list(items[start : start + size])


def publish(
    client: EventsClient,
    entries: Sequence[dict[str, Any]],
    *,
    max_attempts: int = 5,
    base_delay: float = 0.2,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    """Publish all entries or raise PublishError with those that never succeeded.

    Only entries reported in `Entries[i].ErrorCode` are retried; successful ones are not re-sent.
    """
    for batch in chunked(entries):
        pending = batch
        for attempt in range(1, max_attempts + 1):
            response = client.put_events(Entries=pending)
            if int(response.get("FailedEntryCount", 0)) == 0:
                pending = []
                break
            results = response.get("Entries", [])
            pending = [
                entry
                for entry, result in zip(pending, results, strict=True)
                if "ErrorCode" in result
            ]
            if attempt < max_attempts:
                sleep(base_delay * (2 ** (attempt - 1)) * (1 + random.random() * 0.25))
        if pending:
            raise PublishError(pending)
