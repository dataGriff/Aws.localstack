"""Every generator fixture must validate (or fail to validate) against its contract."""

from __future__ import annotations

from pathlib import Path

import pytest

from shared.contracts import ContractViolationError, validate
from tests.helpers.fixtures import fixture_files, load

THIRD_PARTY = "thirdparty/payment.succeeded.schema.json"
VALID_FOLDERS = ("valid", "duplicates", "out_of_order")


@pytest.mark.parametrize(
    "path", [p for folder in VALID_FOLDERS for p in fixture_files(folder)], ids=lambda p: p.name
)
def test_valid_fixture_matches_contract(path: Path) -> None:
    validate(THIRD_PARTY, load(path))


@pytest.mark.parametrize("path", fixture_files("invalid"), ids=lambda p: p.name)
def test_invalid_fixture_is_rejected(path: Path) -> None:
    with pytest.raises(ContractViolationError) as excinfo:
        validate(THIRD_PARTY, load(path))
    assert excinfo.value.errors


def test_each_folder_has_fixtures() -> None:
    for folder in (*VALID_FOLDERS, "invalid"):
        assert fixture_files(folder), f"{folder}/ has no fixtures"


def test_duplicates_share_one_provider_id() -> None:
    ids = {load(p)["id"] for p in fixture_files("duplicates")}
    assert len(ids) == 1
    assert len(fixture_files("duplicates")) >= 2


def test_out_of_order_fixtures_are_delivered_against_created_order() -> None:
    created = [load(p)["created"] for p in fixture_files("out_of_order")]
    assert created == sorted(created, reverse=True)
