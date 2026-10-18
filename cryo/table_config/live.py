"""Bounded live read of a table's configuration from its Source catalog.

Reads metadata fields only (properties, schema, spec, sort order, refs, logs). Never lists
manifests or data files, so it is safe on a request path.
"""

from __future__ import annotations

import threading
import time
import uuid
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeoutError
from dataclasses import dataclass, field
from typing import Any

from cryo.table_config.context import RefInfo

CATALOG_TIMEOUT_SECONDS = 20.0
_CACHE_TTL_SECONDS = 30.0
_BLOOM_INELIGIBLE_TYPES = frozenset({"boolean", "float", "double"})


@dataclass(frozen=True)
class SpecField:
    name: str
    source_column: str | None
    transform: str


@dataclass(frozen=True)
class SortFieldInfo:
    column: str | None
    transform: str
    direction: str
    null_order: str


@dataclass(frozen=True)
class LiveTableConfig:
    properties: dict[str, str]
    format_version: int | None
    table_uuid: str | None = None
    location: str | None = None
    metadata_location: str | None = None
    last_updated_ms: int | None = None
    current_snapshot_id: int | None = None
    snapshot_count: int = 0
    metadata_log_count: int = 0
    schema_id: int | None = None
    schema_count: int = 0
    column_count: int = 0
    bloom_candidate_columns: tuple[str, ...] = ()
    identifier_fields: tuple[str, ...] = ()
    partition_fields: tuple[SpecField, ...] = ()
    spec_id: int | None = None
    spec_count: int = 0
    sort_fields: tuple[SortFieldInfo, ...] = ()
    sort_order_id: int | None = None
    refs: tuple[RefInfo, ...] = field(default_factory=tuple)
    statistics_files: int = 0
    partition_statistics_files: int = 0


class CatalogReadError(Exception):
    """The catalog could not be read in time. Callers fall back to scan data."""


_executor: ThreadPoolExecutor | None = None
_cache: dict[uuid.UUID, tuple[float, LiveTableConfig]] = {}
_cache_lock = threading.Lock()


def _pool() -> ThreadPoolExecutor:
    global _executor
    if _executor is None:
        _executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="cryo-table-config")
    return _executor


def run_bounded[R](fn: Callable[[], R], *, description: str) -> R:
    future = _pool().submit(fn)
    try:
        return future.result(timeout=CATALOG_TIMEOUT_SECONDS)
    except FuturesTimeoutError as exc:
        raise CatalogReadError(f"{description} exceeded {CATALOG_TIMEOUT_SECONDS:.0f}s") from exc
    except CatalogReadError:
        raise
    except Exception as exc:
        raise CatalogReadError(f"{type(exc).__name__}: {exc}"[:500]) from exc


def cached(table_id: uuid.UUID) -> LiveTableConfig | None:
    with _cache_lock:
        entry = _cache.get(table_id)
        if entry is None or entry[0] < time.monotonic():
            _cache.pop(table_id, None)
            return None
        return entry[1]


def remember(table_id: uuid.UUID, config: LiveTableConfig) -> None:
    with _cache_lock:
        _cache[table_id] = (time.monotonic() + _CACHE_TTL_SECONDS, config)


def forget(table_id: uuid.UUID) -> None:
    with _cache_lock:
        _cache.pop(table_id, None)


def load_iceberg_table(source: Mapping[str, Any], schema_name: str, table_name: str) -> Any:
    from cryo.sources.factory import source_catalog_for

    return source_catalog_for(source).load_table(schema_name, table_name)


def read_live_config(
    source: Mapping[str, Any], schema_name: str, table_name: str
) -> LiveTableConfig:
    return run_bounded(
        lambda: extract_config(load_iceberg_table(source, schema_name, table_name)),
        description=f"catalog.load_table({schema_name}.{table_name})",
    )


def extract_config(table: Any) -> LiveTableConfig:
    metadata = table.metadata
    schema = table.schema()
    names = _column_names(schema)
    leaves = _leaf_count(schema)
    spec = table.spec()
    sort_order = table.sort_order()
    return LiveTableConfig(
        properties={str(k): str(v) for k, v in (metadata.properties or {}).items()},
        format_version=_int(getattr(metadata, "format_version", None)),
        table_uuid=str(metadata.table_uuid) if getattr(metadata, "table_uuid", None) else None,
        location=getattr(metadata, "location", None),
        metadata_location=getattr(table, "metadata_location", None),
        last_updated_ms=_int(getattr(metadata, "last_updated_ms", None)),
        current_snapshot_id=_int(getattr(metadata, "current_snapshot_id", None)),
        snapshot_count=len(getattr(metadata, "snapshots", None) or []),
        metadata_log_count=len(getattr(metadata, "metadata_log", None) or []),
        schema_id=_int(getattr(schema, "schema_id", None)),
        schema_count=len(getattr(metadata, "schemas", None) or []),
        column_count=leaves,
        bloom_candidate_columns=tuple(
            field.name
            for field in getattr(schema, "fields", ())
            if _is_primitive(field.field_type)
            and str(field.field_type).lower() not in _BLOOM_INELIGIBLE_TYPES
        ),
        identifier_fields=tuple(
            names.get(field_id, str(field_id))
            for field_id in sorted(getattr(schema, "identifier_field_ids", None) or [])
        ),
        partition_fields=tuple(
            SpecField(
                name=str(part.name),
                source_column=names.get(part.source_id),
                transform=str(part.transform),
            )
            for part in getattr(spec, "fields", ())
        ),
        spec_id=_int(getattr(spec, "spec_id", None)),
        spec_count=len(getattr(metadata, "partition_specs", None) or []),
        sort_fields=tuple(
            SortFieldInfo(
                column=names.get(sort_field.source_id),
                transform=str(sort_field.transform),
                direction=_enum_value(sort_field.direction),
                null_order=_enum_value(sort_field.null_order),
            )
            for sort_field in getattr(sort_order, "fields", ())
        ),
        sort_order_id=_int(getattr(sort_order, "order_id", None)),
        refs=tuple(
            RefInfo(
                name=str(name),
                type="tag" if _enum_value(ref.snapshot_ref_type) == "tag" else "branch",
                snapshot_id=_int(ref.snapshot_id),
                max_ref_age_ms=_int(ref.max_ref_age_ms),
                max_snapshot_age_ms=_int(ref.max_snapshot_age_ms),
                min_snapshots_to_keep=_int(ref.min_snapshots_to_keep),
            )
            for name, ref in sorted((getattr(metadata, "refs", None) or {}).items())
        ),
        statistics_files=len(getattr(metadata, "statistics", None) or []),
        partition_statistics_files=len(getattr(metadata, "partition_statistics", None) or []),
    )


def _column_names(schema: Any) -> dict[int, str]:
    try:
        from pyiceberg.schema import index_name_by_id

        return {int(k): str(v) for k, v in index_name_by_id(schema).items()}
    except Exception:
        return {int(f.field_id): str(f.name) for f in getattr(schema, "fields", ())}


def _leaf_count(schema: Any) -> int:
    try:
        from pyiceberg.schema import index_by_id

        return sum(1 for field in index_by_id(schema).values() if _is_primitive(field.field_type))
    except Exception:
        return len(getattr(schema, "fields", ()))


def _is_primitive(field_type: Any) -> bool:
    return bool(getattr(field_type, "is_primitive", False))


def _enum_value(value: Any) -> str:
    return str(getattr(value, "value", value))


def _int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
