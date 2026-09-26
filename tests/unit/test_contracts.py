from __future__ import annotations

import pytest

from shared.contracts import ContractViolationError, schema_for_envelope, validate
from tests.helpers.fixtures import first_valid_payload


def test_valid_payload_passes() -> None:
    validate("thirdparty/payment.succeeded.schema.json", first_valid_payload())


def test_errors_are_collected_with_paths() -> None:
    payload = first_valid_payload()
    payload["data"]["object"]["amount"] = -1
    payload["data"]["object"]["currency"] = "GBP"
    with pytest.raises(ContractViolationError) as excinfo:
        validate("thirdparty/payment.succeeded.schema.json", payload)
    joined = " ".join(excinfo.value.errors)
    assert "data/object/amount" in joined
    assert "data/object/currency" in joined


def test_schema_lookup_by_kind() -> None:
    assert schema_for_envelope("event", "PaymentReceived") == "events/PaymentReceived.schema.json"
    assert (
        schema_for_envelope("command", "ReconcileInvoice")
        == "commands/ReconcileInvoice.schema.json"
    )


def test_unknown_schema_raises() -> None:
    with pytest.raises(FileNotFoundError):
        validate("events/Nope.schema.json", {})
