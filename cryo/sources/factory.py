from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from cryo.sources.base import SourceCatalog
from cryo.sources.glue import GlueSourceCatalog
from cryo.sources.hive import HiveSourceCatalog
from cryo.sources.rest import RestSourceCatalog

REST_SOURCE_TYPES = {"rest", "iceberg-rest", "iceberg_rest"}
HIVE_SOURCE_TYPES = {"hive", "hms", "hive-metastore", "hive_metastore"}
GLUE_SOURCE_TYPES = {"glue", "aws-glue", "aws_glue"}


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
    if normalized in HIVE_SOURCE_TYPES:
        return HiveSourceCatalog(name=name, config=config)
    if normalized in GLUE_SOURCE_TYPES:
        return GlueSourceCatalog(name=name, config=config)
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
