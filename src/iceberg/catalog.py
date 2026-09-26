"""Build the PyIceberg REST catalog from environment variables only.

AWS (S3 Tables' Iceberg REST endpoint):
    ICEBERG_REST_URI=https://s3tables.<region>.amazonaws.com/iceberg
    ICEBERG_WAREHOUSE=arn:aws:s3tables:<region>:<account>:bucket/<name>
    ICEBERG_SIGV4=true  ICEBERG_SIGNING_NAME=s3tables  (region from AWS_REGION)
LocalStack:
    ICEBERG_REST_URI=http://glue.localhost.localstack.cloud:4566/iceberg
    ICEBERG_WAREHOUSE=<account>:s3tablescatalog/<name>
    ICEBERG_SIGV4=false  ICEBERG_S3_ENDPOINT=http://s3.localhost.localstack.cloud:4566
The writer, the table setup and the query tool all call load_catalog(); nothing else differs.
"""

from __future__ import annotations

from typing import Any

from pyiceberg.catalog import Catalog, load_catalog

from iceberg.schema import DEFAULT_TABLES
from shared.config import env, env_bool, env_optional


def catalog_properties() -> dict[str, Any]:
    props: dict[str, Any] = {
        "type": "rest",
        "uri": env("ICEBERG_REST_URI"),
        "warehouse": env("ICEBERG_WAREHOUSE"),
    }
    region = env_optional("ICEBERG_SIGNING_REGION") or env_optional("AWS_REGION")
    if env_bool("ICEBERG_SIGV4", default=True):
        props["rest.sigv4-enabled"] = "true"
        props["rest.signing-name"] = env_optional("ICEBERG_SIGNING_NAME", "s3tables")
        if region:
            props["rest.signing-region"] = region
    if region:
        props["s3.region"] = region
    s3_endpoint = env_optional("ICEBERG_S3_ENDPOINT")
    if s3_endpoint:
        props["s3.endpoint"] = s3_endpoint
        props["s3.path-style-access"] = "true"
    for key, prop in (
        ("AWS_ACCESS_KEY_ID", "s3.access-key-id"),
        ("AWS_SECRET_ACCESS_KEY", "s3.secret-access-key"),
        ("AWS_SESSION_TOKEN", "s3.session-token"),
    ):
        value = env_optional(key)
        if value and not env_bool("ICEBERG_SIGV4", default=True):
            # Only for the emulator; in AWS the catalog vends scoped credentials per table.
            props[prop] = value
    return props


def namespace() -> str:
    return env("ICEBERG_NAMESPACE", "archive")


def table_names() -> dict[str, str]:
    return {
        "ingress": env("ARCHIVE_TABLE_INGRESS", DEFAULT_TABLES["ingress"]),
        "domain": env("ARCHIVE_TABLE_DOMAIN", DEFAULT_TABLES["domain"]),
    }


def load() -> Catalog:
    return load_catalog("archive", **catalog_properties())
