"""CloudFormation custom resource handler (same image as the writer): ensure namespace + tables.

Create/Update: idempotently create what is missing. Delete: leave data in place (archives are
retained); the S3 table bucket's own removal policy decides what happens to the storage.
"""

from __future__ import annotations

from typing import Any

from iceberg import catalog as catalog_config
from iceberg.setup import ensure_tables
from shared.logging import get_logger

logger = get_logger("table-setup")


def handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    request_type = event.get("RequestType", "Create")
    props = event.get("ResourceProperties", {})
    namespace = props.get("Namespace") or catalog_config.namespace()
    tables = list(props.get("Tables") or catalog_config.table_names().values())
    physical_id = f"{namespace}:{','.join(sorted(tables))}"
    logger.info(
        "table setup", extra={"requestType": request_type, "namespace": namespace, "tables": tables}
    )
    if request_type == "Delete":
        return {"PhysicalResourceId": event.get("PhysicalResourceId", physical_id), "Data": {}}

    locations = ensure_tables(catalog_config.load(), namespace, tables)
    return {
        "PhysicalResourceId": physical_id,
        "Data": {"Namespace": namespace, **{f"Table{n}": loc for n, loc in locations.items()}},
    }
