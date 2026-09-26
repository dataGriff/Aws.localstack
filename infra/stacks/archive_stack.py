"""Iceberg archive: S3 table bucket, writer Lambda (container image), ESM, table setup resource."""

from __future__ import annotations

import aws_cdk as cdk
from aws_cdk import aws_iam as iam
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_lambda_event_sources as sources
from aws_cdk import aws_logs as logs
from aws_cdk import aws_s3 as s3
from aws_cdk import aws_s3tables as s3tables
from aws_cdk import aws_sns as sns
from aws_cdk import aws_sqs as sqs
from aws_cdk import custom_resources as cr
from constructs import Construct

from infra.config import EnvConfig
from infra.stacks.common import (
    REPO_ROOT,
    execution_role,
    function_errors_alarm,
    powertools_env,
    retention,
)

NAMESPACE = "archive"
TABLES = {"ingress": "ingress_events", "domain": "domain_events"}


class ArchiveStack(cdk.Stack):
    def __init__(
        self,
        scope: Construct,
        id_: str,
        cfg: EnvConfig,
        *,
        archive_queue: sqs.IQueue,
        quarantine_bucket: s3.IBucket,
        alarms: sns.ITopic,
        **kwargs: object,
    ) -> None:
        super().__init__(scope, id_, **kwargs)  # type: ignore[arg-type]

        bucket_name = cfg.resource_name("archive")
        table_bucket = s3tables.CfnTableBucket(
            self,
            "TableBucket",
            table_bucket_name=bucket_name,
            encryption_configuration=s3tables.CfnTableBucket.EncryptionConfigurationProperty(
                sse_algorithm="AES256"
            ),
            # S3 Tables maintenance (compaction, snapshot expiry, unreferenced file removal)
            # is managed by the service; there is no compaction job in this repo.
            unreferenced_file_removal=s3tables.CfnTableBucket.UnreferencedFileRemovalProperty(
                status="Enabled", noncurrent_days=10, unreferenced_days=3
            ),
        )
        table_bucket.apply_removal_policy(
            cdk.RemovalPolicy.DESTROY if cfg.is_local else cdk.RemovalPolicy.RETAIN
        )

        # Catalog configuration: the only thing that differs between AWS and LocalStack.
        rest_uri = cfg.iceberg_rest_uri or (
            f"https://s3tables.{cdk.Aws.REGION}.amazonaws.com/iceberg"
        )
        warehouse = (
            f"{cdk.Aws.ACCOUNT_ID}:s3tablescatalog/{bucket_name}"
            if cfg.is_local
            else table_bucket.attr_table_bucket_arn
        )
        iceberg_env = {
            "ICEBERG_REST_URI": rest_uri,
            "ICEBERG_WAREHOUSE": warehouse,
            "ICEBERG_NAMESPACE": NAMESPACE,
            "ICEBERG_SIGV4": "true" if cfg.iceberg_sigv4 else "false",
            "ICEBERG_SIGNING_NAME": "s3tables",
            "ARCHIVE_TABLE_INGRESS": TABLES["ingress"],
            "ARCHIVE_TABLE_DOMAIN": TABLES["domain"],
        }
        if cfg.iceberg_s3_endpoint:
            iceberg_env["ICEBERG_S3_ENDPOINT"] = cfg.iceberg_s3_endpoint

        image = lambda_.DockerImageCode.from_image_asset(
            directory=str(REPO_ROOT),
            file="src/functions/archive_writer/Dockerfile",
            platform=cdk.aws_ecr_assets.Platform.LINUX_AMD64,
            exclude=[
                "cdk.out",
                ".venv",
                "node_modules",
                ".git",
                "build/translator",
                "build/reconcile_invoice",
            ],
        )

        writer_timeout = cdk.Duration.seconds(cfg.archive_writer_timeout_seconds)
        writer = self._image_function(
            cfg,
            "Writer",
            name="archive-writer",
            image=image,
            timeout=writer_timeout,
            memory_mb=2048,
            environment={
                **powertools_env(cfg, "archive-writer"),
                **iceberg_env,
                "QUARANTINE_BUCKET": quarantine_bucket.bucket_name,
            },
        )
        writer.add_event_source(
            sources.SqsEventSource(
                archive_queue,
                batch_size=cfg.archive_batch_size,
                max_batching_window=cdk.Duration.seconds(cfg.archive_batching_window_seconds),
                max_concurrency=cfg.archive_max_concurrency,
                report_batch_item_failures=True,
            )
        )
        writer.add_to_role_policy(
            iam.PolicyStatement(
                actions=["s3:PutObject"],
                resources=[quarantine_bucket.arn_for_objects("archive-writer/*")],
            )
        )
        self._grant_s3tables(writer, table_bucket, write=True)
        function_errors_alarm(self, "archive-writer-errors", writer, alarms, cfg)

        # Table setup: same image, different handler, run as a CloudFormation custom resource.
        setup_fn = self._image_function(
            cfg,
            "TableSetup",
            name="archive-table-setup",
            image=image,
            timeout=cdk.Duration.minutes(5),
            memory_mb=1024,
            environment={**powertools_env(cfg, "table-setup"), **iceberg_env},
            cmd=["functions.archive_writer.table_setup.handler"],
        )
        self._grant_s3tables(setup_fn, table_bucket, write=False, create=True)
        provider_logs = logs.LogGroup(
            self,
            "TableSetupProviderLogs",
            retention=retention(cfg),
            removal_policy=cdk.RemovalPolicy.DESTROY,
        )
        provider = cr.Provider(
            self, "TableSetupProvider", on_event_handler=setup_fn, log_group=provider_logs
        )
        setup = cdk.CustomResource(
            self,
            "Tables",
            service_token=provider.service_token,
            resource_type="Custom::IcebergTables",
            properties={
                "Namespace": NAMESPACE,
                "Tables": sorted(TABLES.values()),
                "SchemaVersion": "1",  # bump to re-run setup after a schema change
            },
        )
        setup.node.add_dependency(table_bucket)

        cdk.CfnOutput(self, "TableBucketArn", value=table_bucket.attr_table_bucket_arn)
        cdk.CfnOutput(self, "TableBucketName", value=bucket_name)
        cdk.CfnOutput(self, "IcebergRestUri", value=rest_uri)
        cdk.CfnOutput(self, "IcebergWarehouse", value=warehouse)
        cdk.CfnOutput(self, "IcebergNamespace", value=NAMESPACE)
        cdk.CfnOutput(self, "IcebergSigV4", value=iceberg_env["ICEBERG_SIGV4"])
        cdk.CfnOutput(self, "IcebergS3Endpoint", value=cfg.iceberg_s3_endpoint or "")
        cdk.CfnOutput(self, "ArchiveWriterFunctionName", value=writer.function_name)

    def _image_function(
        self,
        cfg: EnvConfig,
        id_: str,
        *,
        name: str,
        image: lambda_.DockerImageCode,
        timeout: cdk.Duration,
        memory_mb: int,
        environment: dict[str, str],
        cmd: list[str] | None = None,
    ) -> lambda_.DockerImageFunction:
        function_name = cfg.resource_name(name)
        log_group = logs.LogGroup(
            self,
            f"{id_}Logs",
            log_group_name=f"/aws/lambda/{function_name}",
            retention=retention(cfg),
            removal_policy=cdk.RemovalPolicy.DESTROY,
        )
        role = execution_role(self, f"{id_}Role", cfg, log_group)
        code = image
        if cmd:
            code = lambda_.DockerImageCode.from_image_asset(
                directory=str(REPO_ROOT),
                file="src/functions/archive_writer/Dockerfile",
                platform=cdk.aws_ecr_assets.Platform.LINUX_AMD64,
                cmd=cmd,
                exclude=[
                    "cdk.out",
                    ".venv",
                    "node_modules",
                    ".git",
                    "build/translator",
                    "build/reconcile_invoice",
                ],
            )
        return lambda_.DockerImageFunction(
            self,
            id_,
            function_name=function_name,
            code=code,
            role=role,
            log_group=log_group,
            timeout=timeout,
            memory_size=memory_mb,
            tracing=lambda_.Tracing.ACTIVE,
            architecture=lambda_.Architecture.X86_64,
            environment=environment,
        )

    @staticmethod
    def _grant_s3tables(
        fn: lambda_.Function,
        table_bucket: s3tables.CfnTableBucket,
        *,
        write: bool,
        create: bool = False,
    ) -> None:
        bucket_arn = table_bucket.attr_table_bucket_arn
        tables_arn = f"{bucket_arn}/table/*"
        bucket_actions = [
            "s3tables:GetTableBucket",
            "s3tables:GetNamespace",
            "s3tables:ListNamespaces",
            "s3tables:ListTables",
            "s3tables:GetTable",
            "s3tables:GetTableMetadataLocation",
        ]
        table_actions = [
            "s3tables:GetTable",
            "s3tables:GetTableMetadataLocation",
            "s3tables:GetTableData",
        ]
        if write:
            table_actions += ["s3tables:PutTableData", "s3tables:UpdateTableMetadataLocation"]
        if create:
            bucket_actions += ["s3tables:CreateNamespace", "s3tables:CreateTable"]
            table_actions += ["s3tables:UpdateTableMetadataLocation", "s3tables:PutTableData"]
        fn.add_to_role_policy(
            iam.PolicyStatement(actions=sorted(set(bucket_actions)), resources=[bucket_arn])
        )
        fn.add_to_role_policy(
            iam.PolicyStatement(actions=sorted(set(table_actions)), resources=[tables_arn])
        )
