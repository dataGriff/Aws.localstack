"""Test-wide environment. Set before any Lambda module is imported."""

import os

os.environ.setdefault("POWERTOOLS_TRACE_DISABLED", "1")
os.environ.setdefault("POWERTOOLS_IDEMPOTENCY_DISABLED", "1")
os.environ.setdefault("POWERTOOLS_LOG_LEVEL", "WARNING")
os.environ.setdefault("AWS_DEFAULT_REGION", "eu-west-1")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "test")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "test")
os.environ.setdefault("SERVICE_NAME", "event-platform")
