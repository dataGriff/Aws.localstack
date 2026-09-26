"""Ingress rule -> SQS (+DLQ) -> translator Lambda, with idempotency table and quarantine bucket."""

from __future__ import annotations

import aws_cdk as cdk
from aws_cdk import aws_dynamodb as ddb
from aws_cdk import aws_events as events
from aws_cdk import aws_iam as iam
from aws_cdk import aws_lambda_event_sources as sources
from aws_cdk import aws_s3 as s3
from aws_cdk import aws_sns as sns
from constructs import Construct

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


class TranslatorStack(cdk.Stack):
    quarantine_bucket: s3.Bucket

    def __init__(
        self,
        scope: Construct,
        id_: str,
        cfg: EnvConfig,
        *,
        ingress_bus: events.IEventBus,
        domain_bus: events.IEventBus,
        alarms: sns.ITopic,
        **kwargs: object,
    ) -> None:
        super().__init__(scope, id_, **kwargs)  # type: ignore[arg-type]

        self.quarantine_bucket = s3.Bucket(
            self,
            "Quarantine",
            encryption=s3.BucketEncryption.S3_MANAGED,
            enforce_ssl=True,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            versioned=False,
            removal_policy=cdk.RemovalPolicy.DESTROY if cfg.is_local else cdk.RemovalPolicy.RETAIN,
            auto_delete_objects=cfg.is_local,
            lifecycle_rules=[s3.LifecycleRule(expiration=cdk.Duration.days(90))],
        )

        idempotency = ddb.Table(
            self,
            "Idempotency",
            table_name=cfg.resource_name("translator-idempotency"),
            partition_key=ddb.Attribute(name="id", type=ddb.AttributeType.STRING),
            time_to_live_attribute="expiration",
            billing_mode=ddb.BillingMode.PAY_PER_REQUEST,
            encryption=ddb.TableEncryption.AWS_MANAGED,
            removal_policy=cdk.RemovalPolicy.DESTROY,
        )

        timeout = cdk.Duration.seconds(cfg.translator_timeout_seconds)
        queue, dlq = queue_with_dlq(
            self,
            "TranslatorQueue",
            cfg,
            name="translator",
            visibility_timeout=cdk.Duration.seconds(6 * cfg.translator_timeout_seconds),
        )
        rule_dlq = rule_target_dlq(self, "TranslatorRuleDlq", cfg, name="translator")
        rule(
            self,
            "IngressToTranslator",
            cfg,
            name="ingress-to-translator",
            description="Every third-party event on the ingress bus goes to the translator",
            bus=ingress_bus,
            pattern={"source": [{"prefix": f"{cfg.service_name}.thirdparty."}]},
            targets_=[sqs_target(queue, rule_dlq, cfg)],
        )

        fn = zip_function(
            self,
            "Translator",
            cfg,
            name="translator",
            bundle="translator",
            handler="app.handler",
            timeout=timeout,
            memory_mb=512,
            environment={
                **powertools_env(cfg, "translator"),
                "DOMAIN_BUS_NAME": domain_bus.event_bus_name,
                "IDEMPOTENCY_TABLE": idempotency.table_name,
                "IDEMPOTENCY_TTL_SECONDS": str(cfg.idempotency_ttl_seconds),
                "QUARANTINE_BUCKET": self.quarantine_bucket.bucket_name,
            },
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
            iam.PolicyStatement(actions=["events:PutEvents"], resources=[domain_bus.event_bus_arn])
        )
        # Exactly what Powertools idempotency and the quarantine writer call; no wildcards.
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
        fn.add_to_role_policy(
            iam.PolicyStatement(
                actions=["s3:PutObject"],
                resources=[self.quarantine_bucket.arn_for_objects("translator/*")],
            )
        )

        dlq_depth_alarm(self, "translator-dlq-depth", dlq, alarms, cfg)
        dlq_depth_alarm(self, "translator-rule-dlq-depth", rule_dlq, alarms, cfg)
        function_errors_alarm(self, "translator-errors", fn, alarms, cfg)

        cdk.CfnOutput(self, "TranslatorQueueUrl", value=queue.queue_url)
        cdk.CfnOutput(self, "TranslatorDlqUrl", value=dlq.queue_url)
        cdk.CfnOutput(self, "TranslatorFunctionName", value=fn.function_name)
        cdk.CfnOutput(self, "QuarantineBucketName", value=self.quarantine_bucket.bucket_name)
        cdk.CfnOutput(self, "IdempotencyTableName", value=idempotency.table_name)
