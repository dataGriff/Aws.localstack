"""The shared message envelope used for everything on the domain bus."""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

Kind = Literal["event", "command"]

# Stable EventBridge `source` used for everything the translator publishes.
DOMAIN_SOURCE_SUFFIX = "translator"


def new_id() -> str:
    return str(uuid.uuid4())


def now_iso() -> str:
    return datetime.now(tz=UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def epoch_to_iso(seconds: int) -> str:
    return (
        datetime.fromtimestamp(seconds, tz=UTC)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


@dataclass(frozen=True)
class Envelope:
    kind: Kind
    type: str
    data: dict[str, Any]
    occurred_at: str
    correlation_id: str
    causation_id: str
    source: str
    schema_version: str = "1.0"
    synthetic: bool = False
    id: str = field(default_factory=new_id)

    def to_detail(self) -> dict[str, Any]:
        """Serialise to the wire format placed in EventBridge `detail`."""
        detail: dict[str, Any] = {
            "id": self.id,
            "kind": self.kind,
            "type": self.type,
            "schemaVersion": self.schema_version,
            "occurredAt": self.occurred_at,
            "correlationId": self.correlation_id,
            "causationId": self.causation_id,
            "source": self.source,
            "data": self.data,
        }
        if self.synthetic:
            detail["synthetic"] = True
        return detail

    @classmethod
    def from_detail(cls, detail: dict[str, Any]) -> Envelope:
        try:
            return cls(
                id=detail["id"],
                kind=detail["kind"],
                type=detail["type"],
                schema_version=detail["schemaVersion"],
                occurred_at=detail["occurredAt"],
                correlation_id=detail["correlationId"],
                causation_id=detail["causationId"],
                source=detail["source"],
                synthetic=bool(detail.get("synthetic", False)),
                data=detail["data"],
            )
        except (KeyError, TypeError) as exc:
            raise ValueError(f"not a valid envelope: {exc}") from exc

    def to_eventbridge_entry(self, bus_name: str, service_name: str) -> dict[str, Any]:
        """Build a PutEvents entry. `detail-type` is the message type; `source` is stable."""
        return {
            "EventBusName": bus_name,
            "Source": f"{service_name}.{DOMAIN_SOURCE_SUFFIX}",
            "DetailType": self.type,
            "Time": datetime.fromisoformat(self.occurred_at.replace("Z", "+00:00")),
            "Detail": json.dumps(self.to_detail(), separators=(",", ":")),
        }

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)
