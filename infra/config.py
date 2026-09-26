"""Per-environment settings read from cdk.json context. No account ids or regions live here."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import aws_cdk as cdk


@dataclass(frozen=True)
class EnvConfig:
    name: str
    project: str
    owner: str
    is_local: bool
    log_retention_days: int
    translator_timeout_seconds: int
    handler_timeout_seconds: int
    max_receive_count: int
    rule_retry_attempts: int
    rule_max_event_age_seconds: int
    archive_batch_size: int
    archive_batching_window_seconds: int
    archive_writer_timeout_seconds: int
    archive_max_concurrency: int
    idempotency_ttl_seconds: int
    iceberg_sigv4: bool
    iceberg_rest_uri: str | None
    iceberg_s3_endpoint: str | None
    alarm_email: str | None

    @property
    def service_name(self) -> str:
        return self.project

    def stack_name(self, concern: str) -> str:
        return f"{self.project}-{self.name}-{concern}"

    def resource_name(self, *parts: str) -> str:
        return "-".join([self.project, self.name, *parts])

    @property
    def tags(self) -> dict[str, str]:
        return {"project": self.project, "environment": self.name, "owner": self.owner}


def _context(app: cdk.App, key: str) -> Any:
    """Context from the CLI, falling back to cdk.json so `python infra/app.py` also works."""
    value = app.node.try_get_context(key)
    if value is not None:
        return value
    cdk_json = Path(__file__).resolve().parents[1] / "cdk.json"
    with cdk_json.open(encoding="utf-8") as fh:
        return json.load(fh).get("context", {}).get(key)


def load_config(app: cdk.App) -> EnvConfig:
    env_name = app.node.try_get_context("env") or os.environ.get("CDK_ENV") or "local"
    envs: dict[str, dict[str, Any]] = _context(app, "environments") or {}
    if env_name not in envs:
        raise SystemExit(f"unknown env {env_name!r}; known: {sorted(envs)}")
    raw = envs[env_name]
    return EnvConfig(
        name=env_name,
        project=_context(app, "project"),
        owner=os.environ.get("OWNER") or _context(app, "owner"),
        is_local=bool(raw.get("isLocal", False)),
        log_retention_days=int(raw["logRetentionDays"]),
        translator_timeout_seconds=int(raw["translatorTimeoutSeconds"]),
        handler_timeout_seconds=int(raw["handlerTimeoutSeconds"]),
        max_receive_count=int(raw["maxReceiveCount"]),
        rule_retry_attempts=int(raw["ruleRetryAttempts"]),
        rule_max_event_age_seconds=int(raw["ruleMaxEventAgeSeconds"]),
        archive_batch_size=int(raw["archiveBatchSize"]),
        archive_batching_window_seconds=int(raw["archiveBatchingWindowSeconds"]),
        archive_writer_timeout_seconds=int(raw["archiveWriterTimeoutSeconds"]),
        archive_max_concurrency=int(raw["archiveMaxConcurrency"]),
        idempotency_ttl_seconds=int(raw["idempotencyTtlSeconds"]),
        iceberg_sigv4=bool(raw.get("icebergSigV4", True)),
        iceberg_rest_uri=raw.get("icebergRestUri"),
        iceberg_s3_endpoint=raw.get("icebergS3Endpoint"),
        alarm_email=os.environ.get("ALARM_EMAIL") or raw.get("alarmEmail"),
    )


def cdk_environment() -> cdk.Environment:
    """Account and region come from the caller's credentials / CLI, never from code."""
    return cdk.Environment(
        account=os.environ.get("CDK_DEFAULT_ACCOUNT"),
        region=os.environ.get("CDK_DEFAULT_REGION") or os.environ.get("AWS_REGION"),
    )
