from __future__ import annotations

import json
from pathlib import Path

from cryo.ecosystem.connectors import (
    CONNECTOR_API_VERSION,
    ConnectorManifest,
    check_connector,
    validate_manifest,
)


class SourceFixture:
    def list_tables(self) -> list[tuple[str, str]]:
        return []

    def list_namespaces(self) -> list[str]:
        return []

    def list_all_namespaces(self, *, max_depth: int = 3) -> list[str]:
        return []

    def list_tables_in_namespace(self, namespace: str) -> list[str]:
        return []

    def load_table(self, schema: str, table: str) -> object:
        return object()


def test_ecosystem_source_connector_conforms_without_becoming_core_owned() -> None:
    manifest = ConnectorManifest(
        api_version=CONNECTOR_API_VERSION,
        connector_id="example_source",
        kind="source",
        package="example-cryo-source",
        ownership="ecosystem",
        maintainer="example-org",
        capabilities=("list_tables", "load_table"),
        configuration_schema="https://example.invalid/source.schema.json",
    )

    result = check_connector(manifest, SourceFixture())
    schema = json.loads(
        (Path(__file__).parents[1] / "docs/schemas/connector-manifest-v1.schema.json").read_text()
    )

    assert result.conformant is True
    assert set(manifest.to_dict()) == set(schema["required"])
    assert result.missing_methods == ()
    assert result.blockers == ("requires_external_maintainer_release_and_security_process",)


def test_connector_contract_fails_closed_for_missing_protocol_methods() -> None:
    manifest = ConnectorManifest(
        api_version=CONNECTOR_API_VERSION,
        connector_id="broken_engine",
        kind="engine",
        package="broken-engine",
        ownership="core",
        maintainer="cryo",
        capabilities=("optimize",),
        configuration_schema="https://example.invalid/engine.schema.json",
    )

    result = check_connector(manifest, object())

    assert result.conformant is False
    assert "execute" in result.missing_methods
    assert "connector_protocol_incomplete" in result.blockers


def test_connector_manifest_rejects_unknown_api_versions() -> None:
    manifest = ConnectorManifest(
        api_version="cryo.connector/v2",
        connector_id="future_source",
        kind="source",
        package="future-source",
        ownership="ecosystem",
        maintainer="future-org",
        capabilities=("list_tables",),
        configuration_schema="https://example.invalid/source.schema.json",
    )

    try:
        validate_manifest(manifest)
    except ValueError as error:
        assert "unsupported connector API version" in str(error)
    else:
        raise AssertionError("unknown connector versions must fail")
