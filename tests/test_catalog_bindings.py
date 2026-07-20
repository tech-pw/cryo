"""Unit tests for Trino catalog_bindings helpers."""

from __future__ import annotations

from uuid import uuid4

import pytest

from cryo.domain.enums import EngineType
from cryo.engines.trino.catalog_bindings import (
    destination_serves_source,
    parse_catalog_bindings,
    resolve_trino_catalog,
    validate_catalog_bindings,
)


def test_parse_catalog_bindings_empty() -> None:
    assert parse_catalog_bindings({}) == []
    assert parse_catalog_bindings(None) == []
    assert parse_catalog_bindings({"catalog": "iceberg"}) == []


def test_parse_catalog_bindings_with_override() -> None:
    source_id = uuid4()
    bindings = parse_catalog_bindings(
        {
            "catalog_bindings": [
                {"source_id": str(source_id), "trino_catalog": "iceberg"},
            ]
        }
    )
    assert len(bindings) == 1
    assert bindings[0].source_id == source_id
    assert bindings[0].trino_catalog == "iceberg"


def test_parse_rejects_duplicates_and_bad_shape() -> None:
    source_id = uuid4()
    with pytest.raises(ValueError, match="duplicate"):
        parse_catalog_bindings(
            {
                "catalog_bindings": [
                    {"source_id": str(source_id)},
                    {"source_id": str(source_id)},
                ]
            }
        )
    with pytest.raises(ValueError, match="must be a list"):
        parse_catalog_bindings({"catalog_bindings": {}})
    with pytest.raises(ValueError, match="non-empty"):
        parse_catalog_bindings(
            {"catalog_bindings": [{"source_id": str(source_id), "trino_catalog": "  "}]}
        )


def test_validate_rejects_bindings_on_non_trino() -> None:
    with pytest.raises(ValueError, match="only supported for Trino"):
        validate_catalog_bindings(
            EngineType.SPARK_EMR,
            {"catalog_bindings": [{"source_id": str(uuid4())}]},
        )
    validate_catalog_bindings(EngineType.TRINO, {"catalog_bindings": []})


def test_destination_serves_source() -> None:
    source_a = uuid4()
    source_b = uuid4()
    config = {"catalog_bindings": [{"source_id": str(source_a)}]}
    assert destination_serves_source(EngineType.NATIVE, {}, source_b) is True
    assert destination_serves_source(EngineType.TRINO, config, source_a) is True
    assert destination_serves_source(EngineType.TRINO, config, source_b) is False
    assert destination_serves_source(EngineType.TRINO, {}, source_a) is False
    assert destination_serves_source(EngineType.TRINO, config, None) is False


def test_resolve_trino_catalog_override_and_fallback() -> None:
    source_id = uuid4()
    config = {
        "catalog": "session_default",
        "catalog_bindings": [
            {"source_id": str(source_id), "trino_catalog": "iceberg"},
        ],
    }
    assert (
        resolve_trino_catalog(
            config, source_id=source_id, source_catalog_name="rest"
        )
        == "iceberg"
    )

    config_no_override = {
        "catalog": "session_default",
        "catalog_bindings": [{"source_id": str(source_id)}],
    }
    assert (
        resolve_trino_catalog(
            config_no_override, source_id=source_id, source_catalog_name="rest"
        )
        == "rest"
    )

    assert (
        resolve_trino_catalog(
            {"catalog": "legacy"}, source_id=source_id, source_catalog_name="rest"
        )
        == "legacy"
    )


def test_destination_create_schema_rejects_bad_bindings() -> None:
    from pydantic import ValidationError

    from cryo.api.schemas import DestinationCreate

    with pytest.raises(ValidationError):
        DestinationCreate(
            name="bad",
            engine=EngineType.NATIVE,
            config={"catalog_bindings": [{"source_id": str(uuid4())}]},
        )
