"""Command routing: one rule per command type -> its SQS queue -> its handler Lambda.

Everything here is generated from domain.registry.COMMANDS, so a new command is a registry entry,
a contract, a mapper and a handler module. No payment-specific names appear in this file.
"""

from __future__ import annotations

import aws_cdk as cdk
from aws_cdk import aws_dynamodb as ddb
from aws_cdk import aws_events as events
from aws_cdk import aws_iam as iam
from aws_cdk import aws_lambda_event_sources as sources
from aws_cdk import aws_sns as sns
from constructs import Construct

from domain.registry import COMMANDS, CommandSpec
from infra.config import EnvConfig
from infra.stacks.common import (
    dlq_depth_alarm,
    function_errors_alarm,
    powertools_env,
    queue_with_dlq,
    rule,
    rule_target_dlq,
    sqs_target,
    zip_function,
)


class CommandsStack(cdk.Stack):
    def __init__(
        self,
        scope: Construct,
        id_: str,
        cfg: EnvConfig,
        *,
        domain_bus: events.IEventBus,
        alarms: sns.ITopic,
        **kwargs: object,
    ) -> None:
        super().__init__(scope, id_, **kwargs)  # type: ignore[arg-type]

        # One idempotency table shared by all handlers; Powertools prefixes keys per function.
        idempotency = ddb.Table(
            self,
            "Idempotency",
            table_name=cfg.resource_name("commands-idempotency"),
            partition_key=ddb.Attribute(name="id", type=ddb.AttributeType.STRING),
            time_to_live_attribute="expiration",
            billing_mode=ddb.BillingMode.PAY_PER_REQUEST,
            encryption=ddb.TableEncryption.AWS_MANAGED,
            removal_policy=cdk.RemovalPolicy.DESTROY,
        )

        for spec in COMMANDS.values():
            self._command(cfg, spec, domain_bus, alarms, idempotency)

    def _command(
        self,
        cfg: EnvConfig,
        spec: CommandSpec,
        domain_bus: events.IEventBus,
        alarms: sns.ITopic,
        idempotency: ddb.Table,
    ) -> None:
        pascal = "".join(part.title() for part in spec.handler_name.split("-"))
        timeout = cdk.Duration.seconds(cfg.handler_timeout_seconds)

        queue, dlq = queue_with_dlq(
            self,
            f"{pascal}Queue",
            cfg,
            name=spec.handler_name,
            visibility_timeout=cdk.Duration.seconds(6 * cfg.handler_timeout_seconds),
        )
        rule_dlq = rule_target_dlq(self, f"{pascal}RuleDlq", cfg, name=spec.handler_name)
        rule(
            self,
            f"{pascal}Rule",
            cfg,
            name=f"command-{spec.handler_name}",
            description=f"Route {spec.type} commands to the {spec.handler_name} handler",
            bus=domain_bus,
            pattern={"detail": {"kind": ["command"], "type": [spec.type]}},
            targets_=[sqs_target(queue, rule_dlq, cfg)],
        )

        environment = {
            **powertools_env(cfg, spec.handler_name),
            "IDEMPOTENCY_TABLE": idempotency.table_name,
            "IDEMPOTENCY_TTL_SECONDS": str(cfg.idempotency_ttl_seconds),
        }
        result_table: ddb.Table | None = None
        if spec.result_table:
            result_table = ddb.Table(
                self,
                f"{pascal}Results",
                table_name=cfg.resource_name(spec.handler_name, "results"),
                partition_key=ddb.Attribute(name="pk", type=ddb.AttributeType.STRING),
                sort_key=ddb.Attribute(name="sk", type=ddb.AttributeType.STRING),
                billing_mode=ddb.BillingMode.PAY_PER_REQUEST,
                encryption=ddb.TableEncryption.AWS_MANAGED,
                point_in_time_recovery_specification=ddb.PointInTimeRecoverySpecification(
                    point_in_time_recovery_enabled=not cfg.is_local
                ),
                removal_policy=cdk.RemovalPolicy.DESTROY
                if cfg.is_local
                else cdk.RemovalPolicy.RETAIN,
            )
            environment["RESULT_TABLE"] = result_table.table_name

        fn = zip_function(
            self,
            f"{pascal}Function",
            cfg,
            name=spec.handler_name,
            bundle=spec.handler_module,
            handler="app.handler",
            timeout=timeout,
            memory_mb=512,
            environment=environment,
        )
        fn.add_event_source(
            sources.SqsEventSource(
                queue,
                batch_size=10,
                report_batch_item_failures=True,
                max_batching_window=cdk.Duration.seconds(2),
                max_concurrency=5,
            )
        )
        fn.add_to_role_policy(
            iam.PolicyStatement(
                actions=[
                    "dynamodb:GetItem",
                    "dynamodb:PutItem",
                    "dynamodb:UpdateItem",
                    "dynamodb:DeleteItem",
                ],
                resources=[idempotency.table_arn],
            )
        )
        if result_table is not None:
            fn.add_to_role_policy(
                iam.PolicyStatement(
                    actions=["dynamodb:PutItem"], resources=[result_table.table_arn]
                )
            )
            cdk.CfnOutput(self, f"{pascal}TableName", value=result_table.table_name)

        dlq_depth_alarm(self, f"{spec.handler_name}-dlq-depth", dlq, alarms, cfg)
        dlq_depth_alarm(self, f"{spec.handler_name}-rule-dlq-depth", rule_dlq, alarms, cfg)
        function_errors_alarm(self, f"{spec.handler_name}-errors", fn, alarms, cfg)
        cdk.CfnOutput(self, f"{pascal}QueueUrl", value=queue.queue_url)
        cdk.CfnOutput(self, f"{pascal}DlqUrl", value=dlq.queue_url)
        cdk.CfnOutput(self, f"{pascal}FunctionName", value=fn.function_name)
