"""Registry of third-party event types, mappers and command handlers.

Both the runtime (translator, handlers) and the CDK app read this module, so adding a command
means: a mapper, a handler module, a contract, and one entry here. Keep module-level imports to
the standard library so CDK can import it without the Lambda runtime dependencies.
"""

from __future__ import annotations

from dataclasses import dataclass

# EventBridge `source` used by the generator (and the real webhook receiver later).
THIRD_PARTY_PROVIDER = "payments"


@dataclass(frozen=True)
class MapperSpec:
    """A third-party event type and the function that translates it."""

    third_party_type: str
    schema: str  # relative to contracts/schemas
    mapper: str  # dotted path "module:function"


@dataclass(frozen=True)
class CommandSpec:
    """A command type and the handler Lambda that executes it."""

    type: str
    schema: str  # relative to contracts/schemas
    handler_name: str  # kebab-case resource name, e.g. reconcile-invoice
    handler_module: str  # directory under src/functions
    result_table: bool = True  # handler owns a DynamoDB result table


MAPPERS: dict[str, MapperSpec] = {
    "payment.succeeded": MapperSpec(
        third_party_type="payment.succeeded",
        schema="thirdparty/payment.succeeded.schema.json",
        mapper="domain.mappers.payment_succeeded:map_payment_succeeded",
    ),
}

COMMANDS: dict[str, CommandSpec] = {
    "ReconcileInvoice": CommandSpec(
        type="ReconcileInvoice",
        schema="commands/ReconcileInvoice.schema.json",
        handler_name="reconcile-invoice",
        handler_module="reconcile_invoice",
    ),
}

EVENT_SCHEMAS: dict[str, str] = {
    "PaymentReceived": "events/PaymentReceived.schema.json",
}


def third_party_types() -> list[str]:
    return sorted(MAPPERS)
