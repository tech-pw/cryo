from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from cryo.sources.base import SourceCatalog
from cryo.sources.glue import GlueSourceCatalog
from cryo.sources.hive import HiveSourceCatalog
from cryo.sources.rest import RestSourceCatalog

# Polaris and Nessie both speak the standard Iceberg REST Catalog protocol, so they use the same
# adapter as a plain REST catalog — the "type" distinction in the UI is about which product is on
# the other end, not a different wire protocol PyIceberg needs to know about.
REST_SOURCE_TYPES = {"rest", "iceberg-rest", "iceberg_rest", "polaris", "nessie"}
HIVE_SOURCE_TYPES = {"hive", "hms", "hive-metastore", "hive_metastore"}
GLUE_SOURCE_TYPES = {"glue", "aws-glue", "aws_glue"}


def is_hive_source_type(source_type: str) -> bool:
    normalized = source_type.lower().replace("_", "-")
    return normalized in {item.lower().replace("_", "-") for item in HIVE_SOURCE_TYPES}


def source_catalog_for(source: Any) -> SourceCatalog:
    return catalog_from_config(
        _source_value(source, "name"),
        _source_value(source, "type"),
        _source_config(source),
    )


def monitored_source_catalog(source: Any) -> SourceCatalog:
    from cryo.sources.monitored import wrap_catalog

    catalog = source_catalog_for(source)
    source_id = str(getattr(source, "id", None) or _source_value(source, "name"))
    return wrap_catalog(catalog, source_id=source_id)


def catalog_from_config(name: str, source_type: str, config: Mapping[str, Any]) -> SourceCatalog:
    normalized = source_type.lower().replace("_", "-")
    prepared = dict(config)
    if normalized == "polaris":
        prepared = _polaris_rest_properties(prepared)
    if normalized in REST_SOURCE_TYPES:
        return RestSourceCatalog(name=name, config=prepared)
    if normalized in HIVE_SOURCE_TYPES:
        return HiveSourceCatalog(name=name, config=config)
    if normalized in GLUE_SOURCE_TYPES:
        return GlueSourceCatalog(name=name, config=config)
    raise ValueError(f"Unsupported source type {source_type!r}")


def _polaris_rest_properties(config: dict[str, Any]) -> dict[str, Any]:
    """PyIceberg treats warehouse as the Polaris catalog and requires PRINCIPAL_ROLE:ALL."""
    warehouse = config.get("warehouse")
    if warehouse is None or not str(warehouse).strip():
        catalog = config.get("catalog_name") or config.get("catalog")
        if catalog is not None and str(catalog).strip():
            config["warehouse"] = str(catalog).strip()
    scope = config.get("scope")
    if scope is None or not str(scope).strip():
        config["scope"] = "PRINCIPAL_ROLE:ALL"
    return config


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
