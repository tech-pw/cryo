"""Bounded check that a snapshot id exists in current table metadata.

Reads snapshot ids only. Does not list manifests or data files.
"""

from __future__ import annotations

from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeoutError
from typing import Any

_TIMEOUT_SECONDS = 30.0
_executor: ThreadPoolExecutor | None = None


class CatalogMetadataError(Exception):
    """Catalog metadata could not be read. Callers must not treat this as 'absent'."""


def parse_snapshot_id(raw: object) -> int | None:
    """Decimal snapshot id. Rejects bools and non-integers so a rounded float cannot sneak in."""
    if raw is None or isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        return raw if raw >= 0 else None
    if isinstance(raw, str):
        text = raw.strip()
        if not text.isdigit():
            return None
        return int(text)
    return None


def collect_snapshot_ids(metadata: Any) -> set[int]:
    """Ids on the metadata object. Never calls manifests(), scan(), or inspect()."""
    snapshots = getattr(metadata, "snapshots", None)
    if snapshots is None and isinstance(metadata, Mapping):
        snapshots = metadata.get("snapshots")
    if snapshots is None:
        return set()
    if not isinstance(snapshots, (list, tuple)):
        raise CatalogMetadataError("catalog metadata snapshots is not a list")
    found: set[int] = set()
    for snapshot in snapshots:
        raw: object
        if isinstance(snapshot, Mapping):
            raw = snapshot.get("snapshot_id", snapshot.get("snapshot-id"))
        else:
            raw = getattr(snapshot, "snapshot_id", None)
            if raw is None:
                raw = getattr(snapshot, "snapshot-id", None)
        parsed = parse_snapshot_id(raw)
        if parsed is not None:
            found.add(parsed)
    return found


def _pool() -> ThreadPoolExecutor:
    global _executor
    if _executor is None:
        _executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="cryo-snapshot-presence")
    return _executor


def load_current_snapshot_ids(
    source: Mapping[str, Any], schema_name: str, table_name: str
) -> set[int]:
    """One bounded catalog.load_table. Raises CatalogMetadataError on failure or timeout."""

    def _read() -> set[int]:
        from cryo.sources.factory import source_catalog_for

        catalog = source_catalog_for(source)
        iceberg = catalog.load_table(schema_name, table_name)
        metadata = getattr(iceberg, "metadata", None)
        if metadata is None:
            raise CatalogMetadataError("catalog returned a table without metadata")
        return collect_snapshot_ids(metadata)

    future = _pool().submit(_read)
    try:
        return future.result(timeout=_TIMEOUT_SECONDS)
    except FuturesTimeoutError as exc:
        raise CatalogMetadataError(
            f"catalog.load_table({schema_name}.{table_name}) exceeded {_TIMEOUT_SECONDS:.0f}s"
        ) from exc
    except CatalogMetadataError:
        raise
    except Exception as exc:
        raise CatalogMetadataError(f"{type(exc).__name__}: {exc}") from exc
