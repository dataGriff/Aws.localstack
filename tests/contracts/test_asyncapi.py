"""Structural checks on the AsyncAPI document (full spec validation runs via @asyncapi/cli)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from jsonschema import Draft202012Validator

CONTRACTS = Path(__file__).resolve().parents[2] / "contracts"


@pytest.fixture(scope="module")
def doc() -> dict:
    with (CONTRACTS / "asyncapi.yaml").open(encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def test_is_asyncapi_3(doc: dict) -> None:
    assert doc["asyncapi"] == "3.0.0"
    assert {"info", "channels", "operations", "components"} <= doc.keys()


def test_every_message_payload_ref_exists_and_is_a_valid_schema(doc: dict) -> None:
    for name, message in doc["components"]["messages"].items():
        ref = message["payload"]["schema"]["$ref"]
        path = (CONTRACTS / ref).resolve()
        assert path.exists(), f"message {name}: {ref} does not exist"
        schema = json.loads(path.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
        expected_suffix = ref.removeprefix("./")
        assert schema["$id"].endswith(expected_suffix), f"{ref}: $id must mirror the file path"


def test_every_channel_message_and_operation_ref_resolves(doc: dict) -> None:
    def resolve(ref: str) -> dict:
        node = doc
        for part in ref.removeprefix("#/").split("/"):
            assert part in node, f"unresolvable reference {ref}"
            node = node[part]
        return node

    for channel in doc["channels"].values():
        for message in channel.get("messages", {}).values():
            resolve(message["$ref"])
    for op in doc["operations"].values():
        resolve(op["channel"]["$ref"])
        for message in op.get("messages", []):
            resolve(message["$ref"])


def test_domain_messages_all_embed_the_envelope() -> None:
    for folder in ("events", "commands"):
        for path in (CONTRACTS / "schemas" / folder).glob("*.schema.json"):
            schema = json.loads(path.read_text(encoding="utf-8"))
            refs = [part.get("$ref") for part in schema.get("allOf", [])]
            assert "../envelope.schema.json" in refs, f"{path.name} must allOf the envelope"
