"""Post-deploy smoke test: one clearly marked synthetic event, end to end, within a timeout."""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from typing import Any

from iceberg import catalog as catalog_config
from tools.common import stack_outputs
from tools.generator import freshen, load_set, mark_synthetic, send
from tools.query import configure_env_from_outputs


class SmokeFailureError(RuntimeError):
    pass


def wait_for(probe: Callable[[], Any], *, timeout: float, interval: float, what: str) -> Any:
    deadline = time.monotonic() + timeout
    while True:
        result = probe()
        if result:
            return result
        if time.monotonic() >= deadline:
            raise SmokeFailureError(f"timed out after {timeout}s waiting for {what}")
        time.sleep(interval)


def _at_least(items: list[dict[str, Any]], n: int) -> list[dict[str, Any]] | None:
    return items if len(items) >= n else None


def rows_mentioning(catalog: Any, ns: str, table: str, needle: str) -> list[dict[str, Any]]:
    arrow = catalog.load_table((ns, table)).scan().to_arrow()
    return [r for r in arrow.to_pylist() if needle in r["detail"]]


def run(env_name: str, *, timeout: float = 300, interval: float = 10) -> dict[str, Any]:
    outputs = stack_outputs(env_name)
    configure_env_from_outputs(env_name)
    catalog = catalog_config.load()
    ns = outputs["IcebergNamespace"]

    payload = mark_synthetic(freshen(load_set("valid")[0]))
    third_party_id = payload["id"]
    assert payload["synthetic"] is True and third_party_id.startswith("evt_synthetic_")
    send(outputs["IngressBusName"], [payload])
    print(f"sent synthetic {third_party_id} to {outputs['IngressBusName']}")

    ingress = wait_for(
        lambda: rows_mentioning(catalog, ns, "ingress_events", third_party_id),
        timeout=timeout,
        interval=interval,
        what="the synthetic event in ingress_events",
    )
    print(f"ingress_events: {len(ingress)} row(s)")

    domain = wait_for(
        lambda: _at_least(rows_mentioning(catalog, ns, "domain_events", third_party_id), 2),
        timeout=timeout,
        interval=interval,
        what="PaymentReceived and ReconcileInvoice in domain_events (proves the domain bus)",
    )
    types = sorted(r["type"] for r in domain)
    print(f"domain_events: {types}")
    for row in domain:
        if not json.loads(row["detail"]).get("synthetic"):
            raise SmokeFailureError(f"domain message {row['event_id']} lost the synthetic marker")
    return {"thirdPartyId": third_party_id, "ingressRows": len(ingress), "domainTypes": types}
