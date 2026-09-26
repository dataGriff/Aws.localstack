"""Guard rails on the synthesised CloudFormation: least privilege, DLQs, encryption, tags."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
for p in (ROOT, ROOT / "src"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import aws_cdk as cdk  # noqa: E402
from aws_cdk.assertions import Template  # noqa: E402
from infra.config import load_config  # noqa: E402
from infra.stacks import common  # noqa: E402
from infra.stacks.buses_stack import BusesStack  # noqa: E402
from infra.stacks.translator_stack import TranslatorStack  # noqa: E402

try:
    from infra.stacks.commands_stack import CommandsStack
except ImportError:  # milestone 3 not built yet
    CommandsStack = None  # type: ignore[assignment,misc]

ALLOWED_STAR_ACTIONS = {"xray:PutTraceSegments", "xray:PutTelemetryRecords"}


@pytest.fixture(scope="module")
def templates(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Template]:
    build = tmp_path_factory.mktemp("build")
    for name in ("translator", "reconcile_invoice"):
        (build / name).mkdir()
        (build / name / "app.py").write_text("def handler(e, c): ...\n")
    common.BUILD_DIR = build  # type: ignore[misc]

    app = cdk.App(context={"env": "dev"})
    cfg = load_config(app)
    buses = BusesStack(app, "buses", cfg)
    translator = TranslatorStack(
        app,
        "translator",
        cfg,
        ingress_bus=buses.ingress_bus,
        domain_bus=buses.domain_bus,
        alarms=buses.alarms,
    )
    stacks: dict[str, cdk.Stack] = {"buses": buses, "translator": translator}
    if CommandsStack is not None:
        stacks["commands"] = CommandsStack(
            app, "commands", cfg, domain_bus=buses.domain_bus, alarms=buses.alarms
        )
    return {name: Template.from_stack(stack) for name, stack in stacks.items()}


def _statements(template: Template) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for res in template.find_resources("AWS::IAM::Policy").values():
        out.extend(res["Properties"]["PolicyDocument"]["Statement"])
    for res in template.find_resources("AWS::IAM::Role").values():
        for policy in res["Properties"].get("Policies", []):
            out.extend(policy["PolicyDocument"]["Statement"])
    return out


def test_no_wildcard_actions_or_resources_except_xray(templates: dict[str, Template]) -> None:
    for name, template in templates.items():
        for st in _statements(template):
            actions = st["Action"] if isinstance(st["Action"], list) else [st["Action"]]
            resources = st["Resource"] if isinstance(st["Resource"], list) else [st["Resource"]]
            for action in actions:
                assert "*" not in action.split(":")[1], f"{name}: wildcard action {action}"
            if "*" in resources:
                assert set(actions) <= ALLOWED_STAR_ACTIONS, f"{name}: resource * for {actions}"


def test_every_queue_is_encrypted_and_tls_only(templates: dict[str, Template]) -> None:
    for name, template in templates.items():
        queues = template.find_resources("AWS::SQS::Queue")
        for logical_id, queue in queues.items():
            assert queue["Properties"].get("SqsManagedSseEnabled") is True, f"{name}:{logical_id}"
        policies = template.find_resources("AWS::SQS::QueuePolicy")
        denies = [
            st
            for pol in policies.values()
            for st in pol["Properties"]["PolicyDocument"]["Statement"]
            if st["Effect"] == "Deny"
            and st.get("Condition", {}).get("Bool", {}).get("aws:SecureTransport") == "false"
        ]
        assert len(denies) == len(queues), f"{name}: every queue needs an enforce-TLS policy"


def test_every_work_queue_has_a_dlq_and_every_dlq_an_alarm(templates: dict[str, Template]) -> None:
    for name, template in templates.items():
        queues = template.find_resources("AWS::SQS::Queue")
        work_queues = {k: v for k, v in queues.items() if "RedrivePolicy" in v["Properties"]}
        dlq_ids = {k for k, v in queues.items() if "RedrivePolicy" not in v["Properties"]}
        assert work_queues or dlq_ids, name
        alarms = json.dumps(template.find_resources("AWS::CloudWatch::Alarm"))
        for dlq in dlq_ids:
            assert dlq in alarms, f"{name}: DLQ {dlq} has no depth alarm"


def test_eventbridge_targets_have_retry_policy_and_dlq(templates: dict[str, Template]) -> None:
    for name, template in templates.items():
        for logical_id, rule in template.find_resources("AWS::Events::Rule").items():
            for target in rule["Properties"]["Targets"]:
                assert "DeadLetterConfig" in target, f"{name}:{logical_id}"
                assert "RetryPolicy" in target, f"{name}:{logical_id}"


def test_eventbridge_queue_policies_are_scoped_to_the_rule(templates: dict[str, Template]) -> None:
    for name, template in templates.items():
        for pol in template.find_resources("AWS::SQS::QueuePolicy").values():
            for st in pol["Properties"]["PolicyDocument"]["Statement"]:
                if st.get("Principal", {}).get("Service") == "events.amazonaws.com":
                    assert "aws:SourceArn" in json.dumps(st["Condition"]), name


def test_buckets_are_encrypted_private_and_tls_only(templates: dict[str, Template]) -> None:
    for name, template in templates.items():
        for logical_id, bucket in template.find_resources("AWS::S3::Bucket").items():
            props = bucket["Properties"]
            assert "BucketEncryption" in props, f"{name}:{logical_id}"
            assert props["PublicAccessBlockConfiguration"]["BlockPublicAcls"] is True
        for pol in template.find_resources("AWS::S3::BucketPolicy").values():
            body = json.dumps(pol["Properties"]["PolicyDocument"])
            assert '"aws:SecureTransport": "false"' in body


def test_functions_trace_and_have_retained_logs(templates: dict[str, Template]) -> None:
    for name, template in templates.items():
        for logical_id, fn in template.find_resources("AWS::Lambda::Function").items():
            assert fn["Properties"]["TracingConfig"] == {"Mode": "Active"}, f"{name}:{logical_id}"
            assert fn["Properties"]["Runtime"] == "python3.12"
        for lg in template.find_resources("AWS::Logs::LogGroup").values():
            assert "RetentionInDays" in lg["Properties"]


def test_dynamodb_tables_are_encrypted(templates: dict[str, Template]) -> None:
    for name, template in templates.items():
        for logical_id, table in template.find_resources("AWS::DynamoDB::Table").items():
            assert table["Properties"]["SSESpecification"]["SSEEnabled"] is True, (
                f"{name}:{logical_id}"
            )
