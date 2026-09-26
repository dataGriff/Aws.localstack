"""The single definition of the archive table schema, partitioning and Arrow equivalent."""

from __future__ import annotations

import pyarrow as pa
from pyiceberg.partitioning import PartitionField, PartitionSpec
from pyiceberg.schema import Schema
from pyiceberg.transforms import DayTransform
from pyiceberg.types import NestedField, StringType, TimestamptzType

ICEBERG_SCHEMA = Schema(
    NestedField(1, "event_id", StringType(), required=True, doc="EventBridge event id"),
    NestedField(2, "bus", StringType(), required=True, doc="ingress | domain"),
    NestedField(3, "source", StringType(), required=True, doc="EventBridge source"),
    NestedField(4, "detail_type", StringType(), required=True, doc="EventBridge detail-type"),
    NestedField(5, "kind", StringType(), required=False, doc="envelope kind (domain bus only)"),
    NestedField(6, "type", StringType(), required=False, doc="envelope type (domain bus only)"),
    NestedField(7, "correlation_id", StringType(), required=False),
    NestedField(8, "schema_version", StringType(), required=False),
    NestedField(9, "event_time", TimestamptzType(), required=True, doc="occurredAt or event time"),
    NestedField(10, "ingested_at", TimestamptzType(), required=True, doc="writer wall clock"),
    NestedField(11, "detail", StringType(), required=True, doc="EventBridge detail as JSON"),
    identifier_field_ids=[],
)

PARTITION_SPEC = PartitionSpec(
    PartitionField(source_id=9, field_id=1000, transform=DayTransform(), name="event_day")
)

TABLE_PROPERTIES = {
    "write.parquet.compression-codec": "zstd",
    "write.target-file-size-bytes": str(128 * 1024 * 1024),
}

ARROW_SCHEMA = pa.schema(
    [
        pa.field("event_id", pa.string(), nullable=False),
        pa.field("bus", pa.string(), nullable=False),
        pa.field("source", pa.string(), nullable=False),
        pa.field("detail_type", pa.string(), nullable=False),
        pa.field("kind", pa.string(), nullable=True),
        pa.field("type", pa.string(), nullable=True),
        pa.field("correlation_id", pa.string(), nullable=True),
        pa.field("schema_version", pa.string(), nullable=True),
        pa.field("event_time", pa.timestamp("us", tz="UTC"), nullable=False),
        pa.field("ingested_at", pa.timestamp("us", tz="UTC"), nullable=False),
        pa.field("detail", pa.string(), nullable=False),
    ]
)

# One table per bus. Names are overridable so environments can rename without code changes.
DEFAULT_TABLES: dict[str, str] = {"ingress": "ingress_events", "domain": "domain_events"}
