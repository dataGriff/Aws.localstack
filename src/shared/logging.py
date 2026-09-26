"""Structured JSON logging with correlation ids (AWS Lambda Powertools)."""

from __future__ import annotations

from aws_lambda_powertools import Logger, Tracer

from shared.config import env_optional


def _service() -> str:
    return (
        env_optional("POWERTOOLS_SERVICE_NAME") or env_optional("SERVICE_NAME") or "event-platform"
    )


def get_logger(component: str) -> Logger:
    service = _service()
    return Logger(service=f"{service}.{component}", use_rfc3339=True)


def get_tracer(component: str) -> Tracer:
    service = _service()
    return Tracer(service=f"{service}.{component}")
