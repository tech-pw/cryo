from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Literal

CONNECTOR_API_VERSION = "cryo.connector/v1"
ConnectorKind = Literal["source", "engine"]
Ownership = Literal["core", "ecosystem"]
_IDENTIFIER = re.compile(r"^[a-z][a-z0-9_]{1,63}$")


@dataclass(frozen=True, slots=True)
class ConnectorManifest:
    api_version: str
    connector_id: str
    kind: ConnectorKind
    package: str
    ownership: Ownership
    maintainer: str
    capabilities: tuple[str, ...]
    configuration_schema: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class ConnectorConformance:
    connector_id: str
    api_version: str
    conformant: bool
    missing_methods: tuple[str, ...]
    blockers: tuple[str, ...]


def validate_manifest(manifest: ConnectorManifest) -> None:
    if manifest.api_version != CONNECTOR_API_VERSION:
        raise ValueError(f"unsupported connector API version: {manifest.api_version}")
    if not _IDENTIFIER.fullmatch(manifest.connector_id):
        raise ValueError("connector_id must be a lowercase stable identifier")
    if not manifest.package or not manifest.maintainer:
        raise ValueError("package and maintainer are required")
    if not manifest.configuration_schema:
        raise ValueError("configuration_schema is required")
    if not manifest.capabilities:
        raise ValueError("at least one capability is required")
    if len(set(manifest.capabilities)) != len(manifest.capabilities):
        raise ValueError("capabilities must be unique")


def check_connector(
    manifest: ConnectorManifest,
    connector: object,
) -> ConnectorConformance:
    validate_manifest(manifest)
    required = (
        (
            "list_tables",
            "list_namespaces",
            "list_all_namespaces",
            "list_tables_in_namespace",
            "load_table",
        )
        if manifest.kind == "source"
        else (
            "probe_capacity",
            "render",
            "session_setup",
            "execute",
            "execute_plan",
        )
    )
    missing = tuple(name for name in required if not callable(getattr(connector, name, None)))
    blockers: list[str] = []
    if manifest.ownership == "ecosystem":
        blockers.append("requires_external_maintainer_release_and_security_process")
    if missing:
        blockers.append("connector_protocol_incomplete")
    return ConnectorConformance(
        connector_id=manifest.connector_id,
        api_version=manifest.api_version,
        conformant=not missing,
        missing_methods=missing,
        blockers=tuple(blockers),
    )
