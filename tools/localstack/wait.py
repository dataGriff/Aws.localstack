"""Block until LocalStack reports the services we depend on as available (bounded)."""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.request

SERVICES = ("cloudformation", "events", "sqs", "lambda", "dynamodb", "s3", "iam", "logs")


def main() -> int:
    endpoint = os.environ.get("AWS_ENDPOINT_URL", "http://localhost.localstack.cloud:4566")
    deadline = time.monotonic() + float(os.environ.get("LOCALSTACK_WAIT_SECONDS", "180"))
    last = "no response yet"
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(f"{endpoint}/_localstack/health", timeout=5) as resp:
                health = json.load(resp)
            services = health.get("services", {})
            missing = [s for s in SERVICES if services.get(s) not in ("available", "running")]
            edition = health.get("edition", "?")
            if not missing:
                print(f"LocalStack ready (edition={edition}, version={health.get('version')})")
                if edition == "community":
                    print("WARNING: community edition; S3 Tables and image Lambdas need Pro/Base")
                return 0
            last = f"waiting for {missing}"
        except Exception as exc:
            last = str(exc)
        time.sleep(2)
    print(f"LocalStack not ready after timeout: {last}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
