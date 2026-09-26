"""DuckDB over the archive Iceberg tables via PyIceberg scans (always the latest snapshot)."""

from __future__ import annotations

import os
from typing import Any

import duckdb

from iceberg import catalog as catalog_config
from tools.common import stack_outputs


def configure_env_from_outputs(env_name: str) -> None:
    """Populate ICEBERG_* from the Terraform outputs unless already set by the caller."""
    outputs = stack_outputs(env_name)
    mapping = {
        "ICEBERG_REST_URI": "IcebergRestUri",
        "ICEBERG_WAREHOUSE": "IcebergWarehouse",
        "ICEBERG_NAMESPACE": "IcebergNamespace",
        "ICEBERG_SIGV4": "IcebergSigV4",
        "ICEBERG_S3_ENDPOINT": "IcebergS3Endpoint",
    }
    for var, key in mapping.items():
        if var not in os.environ and outputs.get(key):
            os.environ[var] = outputs[key]


def open_connection(
    env_name: str, tables: dict[str, str] | None = None
) -> duckdb.DuckDBPyConnection:
    """Return a DuckDB connection with one view per archive table (PyIceberg scan -> DuckDB)."""
    configure_env_from_outputs(env_name)
    catalog = catalog_config.load()
    ns = catalog_config.namespace()
    tables = tables or catalog_config.table_names()
    con = duckdb.connect()
    for _bus, name in tables.items():
        table = catalog.load_table((ns, name))
        table.scan().to_duckdb(table_name=name, connection=con)
    return con


def run_sql(con: duckdb.DuckDBPyConnection, sql: str) -> list[tuple[Any, ...]]:
    return con.execute(sql).fetchall()
