"""Fixtures for integration tests against the deployed LocalStack stacks.

Resource names come from the CDK outputs file (OUTPUTS_FILE / build/outputs.local.json).
boto3 picks up AWS_ENDPOINT_URL from the environment (set by the Taskfile).
"""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import boto3
import pytest
from tools.common import stack_outputs

from shared.config import service_name

TARGET_ENV = os.environ.get("TARGET_ENV", "local")
pytestmark = pytest.mark.integration


@pytest.fixture(scope="session")
def outputs() -> dict[str, str]:
    return stack_outputs(TARGET_ENV)


@pytest.fixture(scope="session")
def events_client() -> Any:
    return boto3.client("events")


@pytest.fixture(scope="session")
def sqs_client() -> Any:
    return boto3.client("sqs")


@pytest.fixture(scope="session")
def s3_client() -> Any:
    return boto3.client("s3")


@pytest.fixture(scope="session")
def dynamodb() -> Any:
    return boto3.resource("dynamodb")


@pytest.fixture(scope="session")
def run_id() -> str:
    """Unique per test session; every provider id is suffixed with it for fresh state."""
    return uuid.uuid4().hex[:10]


@dataclass
class BusTap:
    """A temporary SQS queue subscribed to every message the translator publishes."""

    queue_url: str
    sqs: Any

    def drain(self) -> list[dict[str, Any]]:
        received: list[dict[str, Any]] = []
        while True:
            resp = self.sqs.receive_message(
                QueueUrl=self.queue_url,
                MaxNumberOfMessages=10,
                WaitTimeSeconds=1,
                VisibilityTimeout=300,
            )
            messages = resp.get("Messages", [])
            if not messages:
                return received
            for m in messages:
                received.append(json.loads(m["Body"]))


@pytest.fixture(scope="session")
def domain_bus_tap(
    outputs: dict[str, str], events_client: Any, sqs_client: Any, run_id: str
) -> Iterator[BusTap]:
    """Rule + queue created by the test session; torn down afterwards."""
    name = f"test-tap-{run_id}"
    queue_url = sqs_client.create_queue(QueueName=name)["QueueUrl"]
    queue_arn = sqs_client.get_queue_attributes(QueueUrl=queue_url, AttributeNames=["QueueArn"])[
        "Attributes"
    ]["QueueArn"]
    bus = outputs["DomainBusName"]
    rule_arn = events_client.put_rule(
        Name=name,
        EventBusName=bus,
        EventPattern=json.dumps({"source": [f"{service_name()}.translator"]}),
        State="ENABLED",
    )["RuleArn"]
    sqs_client.set_queue_attributes(
        QueueUrl=queue_url,
        Attributes={
            "Policy": json.dumps(
                {
                    "Version": "2012-10-17",
                    "Statement": [
                        {
                            "Effect": "Allow",
                            "Principal": {"Service": "events.amazonaws.com"},
                            "Action": "sqs:SendMessage",
                            "Resource": queue_arn,
                            "Condition": {"ArnEquals": {"aws:SourceArn": rule_arn}},
                        }
                    ],
                }
            )
        },
    )
    events_client.put_targets(
        Rule=name, EventBusName=bus, Targets=[{"Id": "tap", "Arn": queue_arn}]
    )
    try:
        yield BusTap(queue_url=queue_url, sqs=sqs_client)
    finally:
        events_client.remove_targets(Rule=name, EventBusName=bus, Ids=["tap"])
        events_client.delete_rule(Name=name, EventBusName=bus)
        sqs_client.delete_queue(QueueUrl=queue_url)


class DomainMessages:
    """Accumulates tapped domain-bus messages across a session, queryable by source id."""

    def __init__(self, tap: BusTap) -> None:
        self.tap = tap
        self.seen: list[dict[str, Any]] = []

    def refresh(self) -> None:
        self.seen.extend(self.tap.drain())

    def for_source(self, source_event_id: str) -> list[dict[str, Any]]:
        self.refresh()
        return [e for e in self.seen if e.get("detail", {}).get("source") == source_event_id]


@pytest.fixture(scope="session")
def domain_messages(domain_bus_tap: BusTap) -> DomainMessages:
    return DomainMessages(domain_bus_tap)
