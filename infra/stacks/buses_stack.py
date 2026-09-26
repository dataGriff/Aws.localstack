"""Ingress and domain event buses, the shared archive queue, and the catch-all archive rules."""

from __future__ import annotations

import aws_cdk as cdk
from aws_cdk import aws_events as events
from aws_cdk import aws_sns as sns
from aws_cdk import aws_sqs as sqs
from constructs import Construct

from infra.config import EnvConfig
from infra.stacks.common import (
    alarm_topic,
    dlq_depth_alarm,
    queue_with_dlq,
    rule,
    rule_target_dlq,
    sqs_target,
)


class BusesStack(cdk.Stack):
    ingress_bus: events.EventBus
    domain_bus: events.EventBus
    archive_queue: sqs.Queue
    archive_dlq: sqs.Queue
    alarms: sns.Topic

    def __init__(self, scope: Construct, id_: str, cfg: EnvConfig, **kwargs: object) -> None:
        super().__init__(scope, id_, **kwargs)  # type: ignore[arg-type]
        self.alarms = alarm_topic(self, cfg)

        self.ingress_bus = events.EventBus(
            self, "IngressBus", event_bus_name=cfg.resource_name("ingress")
        )
        self.domain_bus = events.EventBus(
            self, "DomainBus", event_bus_name=cfg.resource_name("domain")
        )

        # Shared archive queue: visibility >= 6x writer timeout (brief requirement).
        self.archive_queue, self.archive_dlq = queue_with_dlq(
            self,
            "ArchiveQueue",
            cfg,
            name="archive",
            visibility_timeout=cdk.Duration.seconds(6 * cfg.archive_writer_timeout_seconds),
        )
        dlq_depth_alarm(self, "archive-dlq-depth", self.archive_dlq, self.alarms, cfg)

        # Catch-all rule on each bus. The input transformer wraps the full event so the writer
        # knows which bus it came from without inferring it from `source`.
        for bus_key, bus in (("ingress", self.ingress_bus), ("domain", self.domain_bus)):
            rule_dlq = rule_target_dlq(
                self, f"Archive{bus_key.title()}RuleDlq", cfg, name=f"archive-{bus_key}"
            )
            dlq_depth_alarm(self, f"archive-{bus_key}-rule-dlq-depth", rule_dlq, self.alarms, cfg)
            rule(
                self,
                f"Archive{bus_key.title()}Rule",
                cfg,
                name=f"archive-{bus_key}",
                description=f"Archive every event on the {bus_key} bus",
                bus=bus,
                pattern={"source": [{"prefix": ""}]},
                targets_=[
                    sqs_target(
                        self.archive_queue,
                        rule_dlq,
                        cfg,
                        message=events.RuleTargetInput.from_object(
                            {"bus": bus_key, "event": events.EventField.from_path("$")}
                        ),
                    )
                ],
            )

        cdk.CfnOutput(self, "IngressBusName", value=self.ingress_bus.event_bus_name)
        cdk.CfnOutput(self, "IngressBusArn", value=self.ingress_bus.event_bus_arn)
        cdk.CfnOutput(self, "DomainBusName", value=self.domain_bus.event_bus_name)
        cdk.CfnOutput(self, "DomainBusArn", value=self.domain_bus.event_bus_arn)
        cdk.CfnOutput(self, "ArchiveQueueUrl", value=self.archive_queue.queue_url)
        cdk.CfnOutput(self, "ArchiveDlqUrl", value=self.archive_dlq.queue_url)
        cdk.CfnOutput(self, "AlarmTopicArn", value=self.alarms.topic_arn)
