"""Registry export (Terraform input) and the plan policy checker."""

from __future__ import annotations

import json
from typing import Any

from tools.build import bundle, policy_check

from domain.registry import COMMANDS


def test_registry_export_matches_domain_registry(tmp_path: Any, monkeypatch: Any) -> None:
    monkeypatch.setattr(bundle, "BUILD", tmp_path)
    data = json.loads(bundle.export_registry().read_text())
    assert set(data["commands"]) == set(COMMANDS)
    spec = data["commands"]["ReconcileInvoice"]
    assert spec == {
        "handler_name": "reconcile-invoice",
        "handler_module": "reconcile_invoice",
        "result_table": True,
    }
    assert data["third_party_types"] == ["payment.succeeded"]


def _plan(*resources: dict[str, Any]) -> dict[str, Any]:
    return {"planned_values": {"root_module": {"resources": list(resources)}}}


def _res(type_: str, address: str, **values: Any) -> dict[str, Any]:
    return {"mode": "managed", "type": type_, "address": address, "values": values}


def test_policy_checker_flags_wildcards_and_missing_guards() -> None:
    plan = _plan(
        _res(
            "aws_iam_role_policy",
            "bad",
            policy=json.dumps(
                {
                    "Statement": [
                        {"Effect": "Allow", "Action": "s3:*", "Resource": "*"},
                        {
                            "Effect": "Allow",
                            "Action": ["xray:PutTraceSegments", "xray:PutTelemetryRecords"],
                            "Resource": "*",
                        },
                    ]
                }
            ),
            tags_all={"project": "p", "environment": "e", "owner": "o"},
        ),
        _res(
            "aws_sqs_queue",
            "q",
            name="work",
            sqs_managed_sse_enabled=False,
            queue_url="u",
            tags_all={"project": "p"},
        ),
        _res("aws_cloudwatch_event_target", "t", dead_letter_config=[], retry_policy=[]),
    )
    problems = policy_check.check(plan)
    joined = "\n".join(problems)
    assert "wildcard action s3:*" in joined
    assert "resource * for ['s3:*']" in joined
    assert "not encrypted" in joined
    assert "work queue without DLQ" in joined
    assert "missing tags" in joined
    assert "no dead_letter_config" in joined and "no retry_policy" in joined
    assert not [p for p in problems if "xray" in p]


def test_policy_checker_accepts_a_compliant_plan() -> None:
    tags = {"project": "p", "environment": "e", "owner": "o"}
    tls_deny = {
        "Effect": "Deny",
        "Action": "sqs:*",
        "Resource": "arn",
        "Condition": {"Bool": {"aws:SecureTransport": "false"}},
    }
    eb_allow = {
        "Effect": "Allow",
        "Action": "sqs:SendMessage",
        "Resource": "arn",
        "Principal": {"Service": "events.amazonaws.com"},
        "Condition": {"ArnEquals": {"aws:SourceArn": "rule"}},
    }
    plan = _plan(
        _res(
            "aws_sqs_queue",
            "q",
            name="work",
            sqs_managed_sse_enabled=True,
            queue_url="u",
            redrive_policy="{}",
            tags_all=tags,
        ),
        _res(
            "aws_sqs_queue",
            "d",
            name="work-dlq",
            sqs_managed_sse_enabled=True,
            queue_url="d",
            tags_all=tags,
        ),
        _res(
            "aws_sqs_queue_policy",
            "qp",
            queue_url="u",
            policy=json.dumps({"Statement": [tls_deny, eb_allow]}),
        ),
        _res(
            "aws_sqs_queue_policy",
            "dp",
            queue_url="d",
            policy=json.dumps({"Statement": [tls_deny]}),
        ),
        _res(
            "aws_cloudwatch_metric_alarm", "a", dimensions={"QueueName": "work-dlq"}, tags_all=tags
        ),
        _res(
            "aws_lambda_function",
            "f",
            package_type="Zip",
            runtime="python3.12",
            tracing_config=[{"mode": "Active"}],
            tags_all=tags,
        ),
        _res("aws_cloudwatch_log_group", "lg", retention_in_days=14, tags_all=tags),
    )
    assert policy_check.check(plan) == []
