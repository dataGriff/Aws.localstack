"""Environment-variable driven configuration. Nothing here is hardcoded per account or region."""

from __future__ import annotations

import os


class MissingConfigError(RuntimeError):
    """Raised when a required environment variable is absent."""


def env(name: str, default: str | None = None) -> str:
    value = os.environ.get(name, default)
    if value is None or value == "":
        raise MissingConfigError(f"environment variable {name} is required")
    return value


def env_optional(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(name)
    return value if value not in (None, "") else default


def env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    return int(raw) if raw else default


def env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def service_name() -> str:
    """Stable service name used as EventBridge `source` prefix and Powertools service."""
    return env("SERVICE_NAME", "event-platform")
