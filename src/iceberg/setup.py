"""Idempotent namespace and table creation.

Run by the table-setup Lambda that Terraform invokes on every apply, in every environment.
"""

from __future__ import annotations

from pyiceberg.catalog import Catalog
from pyiceberg.table import Table

from iceberg.schema import ICEBERG_SCHEMA, PARTITION_SPEC, TABLE_PROPERTIES


def ensure_tables(catalog: Catalog, namespace: str, tables: list[str]) -> dict[str, str]:
    """Create the namespace and each table if missing. Returns table name -> metadata location.

    Existing tables are left untouched: schema evolution is a deliberate, reviewed change, not
    something a deploy does implicitly.
    """
    catalog.create_namespace_if_not_exists(namespace)
    result: dict[str, str] = {}
    for name in tables:
        table: Table = catalog.create_table_if_not_exists(
            (namespace, name),
            schema=ICEBERG_SCHEMA,
            partition_spec=PARTITION_SPEC,
            properties=TABLE_PROPERTIES,
        )
        result[name] = table.metadata_location
    return result
