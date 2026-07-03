from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from cryo.sources.base import SourceCatalog
from cryo.sources.rest import RestSourceCatalog

REST_SOURCE_TYPES = {"rest", "iceberg-rest", "iceberg_rest"}


def source_catalog_for(source: Any) -> SourceCatalog:
    return catalog_from_config(
        _source_value(source, "name"),
        _source_value(source, "type"),
        _source_config(source),
    )


def catalog_from_config(name: str, source_type: str, config: Mapping[str, Any]) -> SourceCatalog:
    normalized = source_type.lower().replace("_", "-")
    if normalized in REST_SOURCE_TYPES:
        return RestSourceCatalog(name=name, config=config)
    raise ValueError(f"Unsupported source type {source_type!r}")


def _source_value(source: Any, key: str) -> str:
    value = source[key] if isinstance(source, Mapping) else getattr(source, key)
    return str(value)


def _source_config(source: Any) -> Mapping[str, Any]:
    value = (
        source.get("config", {}) if isinstance(source, Mapping) else getattr(source, "config", {})
    )
    if not isinstance(value, Mapping):
        raise TypeError("source config must be a mapping")
    return value
