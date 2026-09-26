"""Construct helpers shared by every stack: least-privilege functions, queues, alarms."""

from __future__ import annotations

from pathlib import Path

import aws_cdk as cdk
from aws_cdk import aws_cloudwatch as cw
from aws_cdk import aws_cloudwatch_actions as cw_actions
from aws_cdk import aws_events as events
from aws_cdk import aws_events_targets as targets
from aws_cdk import aws_iam as iam
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_logs as logs
from aws_cdk import aws_sns as sns
from aws_cdk import aws_sns_subscriptions as subs
from aws_cdk import aws_sqs as sqs
from constructs import Construct

from infra.config import EnvConfig

REPO_ROOT = Path(__file__).resolve().parents[2]
BUILD_DIR = REPO_ROOT / "build"


def retention(cfg: EnvConfig) -> logs.RetentionDays:
    table = {
        1: logs.RetentionDays.ONE_DAY,
        3: logs.RetentionDays.THREE_DAYS,
        7: logs.RetentionDays.ONE_WEEK,
        14: logs.RetentionDays.TWO_WEEKS,
        30: logs.RetentionDays.ONE_MONTH,
        90: logs.RetentionDays.THREE_MONTHS,
    }
    return table.get(cfg.log_retention_days, logs.RetentionDays.TWO_WEEKS)


def queue_with_dlq(
    scope: Construct, id_: str, cfg: EnvConfig, *, name: str, visibility_timeout: cdk.Duration
) -> tuple[sqs.Queue, sqs.Queue]:
    """Encrypted queue plus its DLQ. DLQ keeps messages for the maximum 14 days."""
    dlq = sqs.Queue(
        scope,
        f"{id_}Dlq",
        queue_name=cfg.resource_name(name, "dlq"),
        encryption=sqs.QueueEncryption.SQS_MANAGED,
        enforce_ssl=True,
        retention_period=cdk.Duration.days(14),
    )
    queue = sqs.Queue(
        scope,
        id_,
        queue_name=cfg.resource_name(name),
        encryption=sqs.QueueEncryption.SQS_MANAGED,
        enforce_ssl=True,
        visibility_timeout=visibility_timeout,
        retention_period=cdk.Duration.days(4),
        dead_letter_queue=sqs.DeadLetterQueue(queue=dlq, max_receive_count=cfg.max_receive_count),
    )
    return queue, dlq


def rule_target_dlq(scope: Construct, id_: str, cfg: EnvConfig, *, name: str) -> sqs.Queue:
    """DLQ for events EventBridge could not deliver to a rule target."""
    return sqs.Queue(
        scope,
        id_,
        queue_name=cfg.resource_name(name, "rule-dlq"),
        encryption=sqs.QueueEncryption.SQS_MANAGED,
        enforce_ssl=True,
        retention_period=cdk.Duration.days(14),
    )


def sqs_target(
    queue: sqs.Queue, dlq: sqs.Queue, cfg: EnvConfig, message: events.RuleTargetInput | None = None
) -> targets.SqsQueue:
    """SQS target with its own retry policy and DLQ. CDK adds the queue policy that allows
    events.amazonaws.com conditioned on aws:SourceArn = the rule's ARN."""
    return targets.SqsQueue(
        queue,
        dead_letter_queue=dlq,
        retry_attempts=cfg.rule_retry_attempts,
        max_event_age=cdk.Duration.seconds(cfg.rule_max_event_age_seconds),
        message=message,
    )


def rule(
    scope: Construct,
    id_: str,
    cfg: EnvConfig,
    *,
    name: str,
    description: str,
    bus: events.IEventBus,
    pattern: dict[str, object],
    targets_: list[events.IRuleTarget],
) -> events.Rule:
    """EventBridge rule with a raw event pattern (supports prefix/anything-but matchers that the
    jsii runtime type-checker rejects when passed through events.EventPattern)."""
    r = events.Rule(
        scope,
        id_,
        rule_name=cfg.resource_name(name),
        description=description,
        event_bus=bus,
        event_pattern=events.EventPattern(source=["placeholder"]),
        targets=targets_,
    )
    cfn = r.node.default_child
    assert isinstance(cfn, events.CfnRule)
    cfn.add_property_override("EventPattern", pattern)
    return r


def alarm_topic(scope: Construct, cfg: EnvConfig) -> sns.Topic:
    topic = sns.Topic(scope, "AlarmTopic", topic_name=cfg.resource_name("alarms"), enforce_ssl=True)
    if cfg.alarm_email:
        topic.add_subscription(subs.EmailSubscription(cfg.alarm_email))
    return topic


def dlq_depth_alarm(
    scope: Construct, id_: str, queue: sqs.Queue, topic: sns.ITopic, cfg: EnvConfig
) -> cw.Alarm:
    alarm = cw.Alarm(
        scope,
        id_,
        alarm_name=cfg.resource_name(id_),
        alarm_description=f"Messages are stuck in {queue.queue_name}",
        metric=queue.metric_approximate_number_of_messages_visible(
            period=cdk.Duration.minutes(1), statistic="Maximum"
        ),
        threshold=0,
        comparison_operator=cw.ComparisonOperator.GREATER_THAN_THRESHOLD,
        evaluation_periods=1,
        treat_missing_data=cw.TreatMissingData.NOT_BREACHING,
    )
    alarm.add_alarm_action(cw_actions.SnsAction(topic))
    return alarm


def function_errors_alarm(
    scope: Construct, id_: str, fn: lambda_.IFunction, topic: sns.ITopic, cfg: EnvConfig
) -> cw.Alarm:
    alarm = cw.Alarm(
        scope,
        id_,
        alarm_name=cfg.resource_name(id_),
        alarm_description=f"{fn.function_name} reported errors",
        metric=fn.metric_errors(period=cdk.Duration.minutes(5), statistic="Sum"),
        threshold=0,
        comparison_operator=cw.ComparisonOperator.GREATER_THAN_THRESHOLD,
        evaluation_periods=1,
        treat_missing_data=cw.TreatMissingData.NOT_BREACHING,
    )
    alarm.add_alarm_action(cw_actions.SnsAction(topic))
    return alarm


def powertools_env(cfg: EnvConfig, component: str) -> dict[str, str]:
    return {
        "SERVICE_NAME": cfg.service_name,
        "POWERTOOLS_SERVICE_NAME": f"{cfg.service_name}.{component}",
        "POWERTOOLS_LOG_LEVEL": "INFO",
        "POWERTOOLS_LOGGER_LOG_EVENT": "false",
        "POWERTOOLS_TRACER_CAPTURE_RESPONSE": "false",
        "ENVIRONMENT": cfg.name,
    }


def execution_role(
    scope: Construct, id_: str, cfg: EnvConfig, log_group: logs.LogGroup
) -> iam.Role:
    """Lambda role without AWSLambdaBasicExecutionRole: logs are scoped to the function's group.
    X-Ray's two write actions only accept resource '*' (service limitation)."""
    role = iam.Role(scope, id_, assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"))
    role.add_to_policy(
        iam.PolicyStatement(
            actions=["logs:CreateLogStream", "logs:PutLogEvents"],
            resources=[log_group.log_group_arn, f"{log_group.log_group_arn}:*"],
        )
    )
    role.add_to_policy(
        iam.PolicyStatement(
            actions=["xray:PutTraceSegments", "xray:PutTelemetryRecords"],
            resources=["*"],
        )
    )
    return role


def zip_function(
    scope: Construct,
    id_: str,
    cfg: EnvConfig,
    *,
    name: str,
    bundle: str,
    handler: str,
    environment: dict[str, str],
    timeout: cdk.Duration,
    memory_mb: int = 512,
) -> lambda_.Function:
    """Python 3.12 zip Lambda from build/<bundle> with tracing, JSON logs and log retention."""
    function_name = cfg.resource_name(name)
    log_group = logs.LogGroup(
        scope,
        f"{id_}Logs",
        log_group_name=f"/aws/lambda/{function_name}",
        retention=retention(cfg),
        removal_policy=cdk.RemovalPolicy.DESTROY,
    )
    role = execution_role(scope, f"{id_}Role", cfg, log_group)
    code_dir = BUILD_DIR / bundle
    if not code_dir.is_dir():
        raise SystemExit(f"{code_dir} missing; run `task build` first")
    return lambda_.Function(
        scope,
        id_,
        function_name=function_name,
        runtime=lambda_.Runtime.PYTHON_3_12,
        architecture=lambda_.Architecture.X86_64,
        code=lambda_.Code.from_asset(str(code_dir)),
        handler=handler,
        role=role,
        log_group=log_group,
        timeout=timeout,
        memory_size=memory_mb,
        tracing=lambda_.Tracing.ACTIVE,
        environment=environment,
    )
