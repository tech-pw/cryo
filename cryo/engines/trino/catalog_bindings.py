"""Trino destination ↔ Cryo source (lakehouse) catalog bindings.

Stored on the destination as ``config.catalog_bindings``:

    [{"source_id": "<uuid>", "trino_catalog": "iceberg"}, ...]

``trino_catalog`` is optional; when omitted, SQL uses the source's display
catalog name (``catalog_name_for_source``). ``config.catalog`` remains the
Trino session default for client connect / probes.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from cryo.domain.enums import EngineType


@dataclass(frozen=True)
class CatalogBinding:
    source_id: UUID
    trino_catalog: str | None = None


def parse_catalog_bindings(config: Mapping[str, Any] | None) -> list[CatalogBinding]:
    """Parse ``catalog_bindings`` from a destination config. Empty if missing."""
    if not config:
        return []
    raw = config.get("catalog_bindings")
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ValueError("config.catalog_bindings must be a list")
    bindings: list[CatalogBinding] = []
    seen: set[UUID] = set()
    for index, item in enumerate(raw):
        if not isinstance(item, Mapping):
            raise ValueError(f"config.catalog_bindings[{index}] must be an object")
        source_raw = item.get("source_id")
        if source_raw is None:
            raise ValueError(f"config.catalog_bindings[{index}].source_id is required")
        try:
            source_id = UUID(str(source_raw))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"config.catalog_bindings[{index}].source_id must be a UUID") from exc
        if source_id in seen:
            raise ValueError(f"config.catalog_bindings: duplicate source_id {source_id}")
        seen.add(source_id)
        trino_catalog = item.get("trino_catalog")
        if trino_catalog is not None:
            if not isinstance(trino_catalog, str) or not trino_catalog.strip():
                raise ValueError(
                    f"config.catalog_bindings[{index}].trino_catalog must be a "
                    "non-empty string when set"
                )
            trino_catalog = trino_catalog.strip()
        bindings.append(CatalogBinding(source_id=source_id, trino_catalog=trino_catalog))
    return bindings


def validate_catalog_bindings(engine: EngineType | str, config: Mapping[str, Any] | None) -> None:
    """Shape-validate catalog_bindings; reject on non-Trino engines."""
    if not config or "catalog_bindings" not in config:
        return
    engine_value = engine.value if isinstance(engine, EngineType) else str(engine)
    if engine_value != EngineType.TRINO.value:
        raise ValueError("config.catalog_bindings is only supported for Trino engines")
    parse_catalog_bindings(config)


def catalog_bindings_as_dicts(bindings: Sequence[CatalogBinding]) -> list[dict[str, str]]:
    """Serialize bindings for storage / API responses."""
    out: list[dict[str, str]] = []
    for binding in bindings:
        entry: dict[str, str] = {"source_id": str(binding.source_id)}
        if binding.trino_catalog:
            entry["trino_catalog"] = binding.trino_catalog
        out.append(entry)
    return out


def destination_serves_source(
    engine: EngineType | str,
    config: Mapping[str, Any] | None,
    source_id: UUID | str | None,
) -> bool:
    """Return whether a destination may run maintenance for a table's source.

    Non-Trino engines always serve any source. Trino requires an explicit
    ``catalog_bindings`` entry for ``source_id``. Empty bindings ⇒ serves none.
    """
    engine_value = engine.value if isinstance(engine, EngineType) else str(engine)
    if engine_value != EngineType.TRINO.value:
        return True
    if source_id is None:
        return False
    try:
        sid = UUID(str(source_id))
    except (TypeError, ValueError):
        return False
    try:
        bindings = parse_catalog_bindings(config)
    except ValueError:
        return False
    return any(binding.source_id == sid for binding in bindings)


def resolve_trino_catalog(
    config: Mapping[str, Any] | None,
    *,
    source_id: UUID | str | None = None,
    source_catalog_name: str | None = None,
) -> str | None:
    """Resolve the Trino SQL catalog name for a table.

    Resolution order when ``source_id`` matches a binding:
      1. binding.trino_catalog (override)
      2. source_catalog_name
    When no bindings are configured (legacy): ``config.catalog``.
    When bindings exist but source is unbound: prefer ``source_catalog_name``,
    else ``config.catalog``.
    """
    config = config or {}
    try:
        bindings = parse_catalog_bindings(config)
    except ValueError:
        bindings = []

    sid: UUID | None = None
    if source_id is not None:
        try:
            sid = UUID(str(source_id))
        except (TypeError, ValueError):
            sid = None

    if sid is not None and bindings:
        for binding in bindings:
            if binding.source_id == sid:
                if binding.trino_catalog:
                    return binding.trino_catalog
                if isinstance(source_catalog_name, str) and source_catalog_name:
                    return source_catalog_name
                break
        else:
            if isinstance(source_catalog_name, str) and source_catalog_name:
                return source_catalog_name

    if not bindings:
        catalog = config.get("catalog") or config.get("catalog_name")
        if isinstance(catalog, str) and catalog:
            return catalog
        if isinstance(source_catalog_name, str) and source_catalog_name:
            return source_catalog_name
        return None

    catalog = config.get("catalog") or config.get("catalog_name")
    if isinstance(catalog, str) and catalog:
        return catalog
    if isinstance(source_catalog_name, str) and source_catalog_name:
        return source_catalog_name
    return None


def bound_source_ids(config: Mapping[str, Any] | None) -> set[UUID]:
    """Return the set of source IDs linked on a destination (empty if none)."""
    try:
        return {binding.source_id for binding in parse_catalog_bindings(config)}
    except ValueError:
        return set()
