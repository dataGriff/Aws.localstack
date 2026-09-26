"""Deterministic waiting: poll with a hard timeout instead of sleeping blindly."""

from __future__ import annotations

import time
from collections.abc import Callable


class PollTimeoutError(AssertionError):
    pass


def poll_until[T](
    probe: Callable[[], T | None], *, timeout: float, interval: float = 1.0, what: str = "condition"
) -> T:
    """Call `probe` until it returns a truthy value or `timeout` seconds elapse."""
    deadline = time.monotonic() + timeout
    last: T | None = None
    while True:
        last = probe()
        if last:
            return last
        if time.monotonic() >= deadline:
            raise PollTimeoutError(f"timed out after {timeout}s waiting for {what}; last={last!r}")
        time.sleep(interval)


def stays_unchanged[T](
    probe: Callable[[], T], *, quiet: float, interval: float = 1.0, what: str = "value"
) -> T:
    """Assert `probe` returns the same value for `quiet` seconds (bounded negative check)."""
    first = probe()
    deadline = time.monotonic() + quiet
    while time.monotonic() < deadline:
        time.sleep(interval)
        current = probe()
        if current != first:
            raise AssertionError(f"{what} changed during quiet period: {first!r} -> {current!r}")
    return first
