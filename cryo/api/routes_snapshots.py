"""Read-only Iceberg snapshot history for one table.

Loads current metadata through the source adapter and returns. Does not list
manifests or data files, and does not write snapshot lists into stats history.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeoutError
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from cryo.api.deps import SessionDep, require_viewer
from cryo.db.models import Source, Table
from cryo.db.repositories.protected_snapshots import ProtectedSnapshotRepository

router = APIRouter(prefix="/api/v1", tags=["tables"])

# Same bound as layout_analyzer: pyiceberg's Hive Thrift client opens a socket
# with no timeout, so a black-holed metastore must not hold the request open.
_SNAPSHOTS_CATALOG_TIMEOUT_SECONDS = 30.0
_MAX_PAGE_SIZE = 100
_DECIMAL_ID = re.compile(r"-?\d+")

_snapshots_catalog_executor: ThreadPoolExecutor | None = None


def _catalog_executor() -> ThreadPoolExecutor:
    global _snapshots_catalog_executor
    if _snapshots_catalog_executor is None:
        _snapshots_catalog_executor = ThreadPoolExecutor(
            max_workers=4, thread_name_prefix="snapshots-catalog"
        )
    return _snapshots_catalog_executor


def _run_catalog_call_bounded[R](fn: Callable[[], R], *, description: str) -> R:
    future = _catalog_executor().submit(fn)
    try:
        return future.result(timeout=_SNAPSHOTS_CATALOG_TIMEOUT_SECONDS)
    except FuturesTimeoutError as exc:
        raise TimeoutError(
            f"{description} exceeded {_SNAPSHOTS_CATALOG_TIMEOUT_SECONDS:.0f}s and was abandoned"
        ) from exc


class SnapshotWriterRead(BaseModel):
    engine: str | None = None
    engine_version: str | None = None
    user: str | None = None
    query_id: str | None = None
    job_id: str | None = None


class SnapshotSummaryRead(BaseModel):
    total_records: int | None = None
    total_files_size: int | None = None
    added_data_files: int | None = None
    deleted_data_files: int | None = None
    added_records: int | None = None
    deleted_records: int | None = None
    added_files_size: int | None = None
    removed_files_size: int | None = None
    added_delete_files: int | None = None
    removed_delete_files: int | None = None
    added_position_delete_files: int | None = None
    added_equality_delete_files: int | None = None
    added_dvs: int | None = None
    total_delete_files: int | None = None
    total_position_deletes: int | None = None
    total_equality_deletes: int | None = None
    changed_partition_count: int | None = None
    writer: SnapshotWriterRead | None = None


class SnapshotRefRead(BaseModel):
    name: str
    type: Literal["branch", "tag"]
    snapshot_id: str
    min_snapshots_to_keep: int | None = None
    max_snapshot_age_ms: int | None = None
    max_ref_age_ms: int | None = None


class SnapshotRead(BaseModel):
    snapshot_id: str
    parent_snapshot_id: str | None = None
    timestamp_ms: int | None
    committed_at: str | None
    sequence_number: int | None
    operation: str | None
    summary: SnapshotSummaryRead
    is_current: bool
    refs: list[SnapshotRefRead]
    protected: bool
    protected_label: str | None = None


class SnapshotListResponse(BaseModel):
    table_id: uuid.UUID
    current_snapshot_id: str | None
    total: int
    pinned_count: int
    latest_committed_at: str | None
    oldest_committed_at: str | None
    page: int
    page_size: int
    snapshots: list[SnapshotRead]


def _decimal_id(raw: Any) -> str | None:
    if raw is None or isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        return str(raw)
    if isinstance(raw, str):
        text = raw.strip()
        if not _DECIMAL_ID.fullmatch(text):
            return None
        return str(int(text))
    return None


def _decimal_field(obj: Any, attr: str, *aliases: str) -> str | None:
    names = (attr, *aliases)
    if isinstance(obj, Mapping):
        for name in names:
            if name in obj:
                return _decimal_id(obj.get(name))
        return None
    for name in names:
        if hasattr(obj, name):
            return _decimal_id(getattr(obj, name))
    return None


def _optional_int(raw: Any) -> int | None:
    if raw is None or isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        return raw
    text = str(raw).strip()
    if text == "":
        return None
    try:
        return int(text)
    except (TypeError, ValueError):
        return None


def _summary_raw(summary: Any, key: str) -> Any:
    if summary is None:
        return None
    if isinstance(summary, Mapping):
        return summary.get(key)
    getter = getattr(summary, "get", None)
    if callable(getter):
        return getter(key)
    try:
        return summary[key]
    except (KeyError, TypeError, IndexError):
        additional = getattr(summary, "additional_properties", None)
        if isinstance(additional, Mapping):
            return additional.get(key)
    return None


def _operation(summary: Any) -> str | None:
    raw = _summary_raw(summary, "operation")
    if raw is None:
        return None
    value = getattr(raw, "value", raw)
    text = str(value).strip()
    return text or None


def _summary_int(summary: Any, key: str) -> int | None:
    return _optional_int(_summary_raw(summary, key))


def _summary_text(summary: Any, *keys: str) -> str | None:
    for key in keys:
        raw = _summary_raw(summary, key)
        if raw is None:
            continue
        text = str(getattr(raw, "value", raw)).strip()
        if text:
            return text[:200]
    return None


# Summary keys are optional ("should" in the spec), so each engine is recognised by the keys it
# is known to write; anything else stays unattributed rather than guessed.
_ENGINE_MARKERS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("trino", ("trino_query_id", "trino_user")),
    ("spark", ("spark.app.id",)),
    ("flink", ("flink.job-id",)),
    ("kafka-connect", ("kafka.connect.commit-id",)),
)


def _writer(summary: Any) -> SnapshotWriterRead | None:
    engine = _summary_text(summary, "engine-name")
    if engine is None:
        engine = next(
            (
                name
                for name, keys in _ENGINE_MARKERS
                if any(_summary_raw(summary, key) is not None for key in keys)
            ),
            None,
        )
    writer = SnapshotWriterRead(
        engine=engine.lower() if engine else None,
        engine_version=_summary_text(summary, "engine-version"),
        user=_summary_text(summary, "trino_user"),
        query_id=_summary_text(summary, "trino_query_id"),
        job_id=_summary_text(
            summary, "spark.app.id", "flink.job-id", "kafka.connect.commit-id", "app-id"
        ),
    )
    return writer if any(writer.model_dump().values()) else None


def _summary_read(summary: Any) -> SnapshotSummaryRead:
    def value(key: str) -> int | None:
        return _summary_int(summary, key)

    return SnapshotSummaryRead(
        total_records=value("total-records"),
        total_files_size=value("total-files-size"),
        added_data_files=value("added-data-files"),
        deleted_data_files=value("deleted-data-files"),
        added_records=value("added-records"),
        deleted_records=value("deleted-records"),
        added_files_size=value("added-files-size"),
        removed_files_size=value("removed-files-size"),
        added_delete_files=value("added-delete-files"),
        removed_delete_files=value("removed-delete-files"),
        added_position_delete_files=value("added-position-delete-files"),
        added_equality_delete_files=value("added-equality-delete-files"),
        added_dvs=value("added-dvs"),
        total_delete_files=value("total-delete-files"),
        total_position_deletes=value("total-position-deletes"),
        total_equality_deletes=value("total-equality-deletes"),
        changed_partition_count=value("changed-partition-count"),
        writer=_writer(summary),
    )


def _ref_type(ref: Any) -> Literal["branch", "tag"] | None:
    raw = ref.get("type") if isinstance(ref, Mapping) else getattr(ref, "snapshot_ref_type", None)
    if raw is None and isinstance(ref, Mapping):
        raw = ref.get("snapshot_ref_type")
    value = getattr(raw, "value", raw)
    text = str(value).lower() if value is not None else ""
    if text in {"branch", "tag"}:
        return "branch" if text == "branch" else "tag"
    return None


def _attr_int(obj: Any, name: str) -> int | None:
    if isinstance(obj, Mapping):
        if name not in obj:
            return None
        return _optional_int(obj.get(name))
    if not hasattr(obj, name):
        return None
    return _optional_int(getattr(obj, name))


def _committed_at(timestamp_ms: int | None) -> str | None:
    if timestamp_ms is None:
        return None
    return datetime.fromtimestamp(timestamp_ms / 1000, tz=UTC).isoformat()


def _snapshot_id(snapshot: Any) -> str:
    snapshot_id = _decimal_field(snapshot, "snapshot_id", "snapshot-id")
    if snapshot_id is None:
        raise RuntimeError("catalog snapshot is missing snapshot_id")
    return snapshot_id


def _parent_snapshot_id(snapshot: Any) -> str | None:
    return _decimal_field(snapshot, "parent_snapshot_id", "parent-snapshot-id")


def _refs_by_snapshot(metadata: Any) -> dict[str, list[SnapshotRefRead]]:
    refs = getattr(metadata, "refs", None)
    if refs is None:
        return {}
    if not isinstance(refs, Mapping):
        raise RuntimeError("catalog metadata refs is not a mapping")
    grouped: dict[str, list[SnapshotRefRead]] = {}
    for name, ref in refs.items():
        kind = _ref_type(ref)
        snapshot_id = _decimal_field(ref, "snapshot_id", "snapshot-id")
        if kind is None or snapshot_id is None:
            raise RuntimeError(f"snapshot ref {name!r} is missing a type or snapshot id")
        grouped.setdefault(snapshot_id, []).append(
            SnapshotRefRead(
                name=str(name),
                type=kind,
                snapshot_id=snapshot_id,
                min_snapshots_to_keep=_attr_int(ref, "min_snapshots_to_keep"),
                max_snapshot_age_ms=_attr_int(ref, "max_snapshot_age_ms"),
                max_ref_age_ms=_attr_int(ref, "max_ref_age_ms"),
            )
        )
    for items in grouped.values():
        items.sort(key=lambda item: (item.type, item.name))
    return grouped


def _snapshot_row(snapshot: Any, refs: list[SnapshotRefRead]) -> SnapshotRead:
    snapshot_id = _snapshot_id(snapshot)
    timestamp_ms = _attr_int(snapshot, "timestamp_ms")
    if isinstance(snapshot, Mapping):
        summary = snapshot.get("summary")
    else:
        summary = getattr(snapshot, "summary", None)
    return SnapshotRead(
        snapshot_id=snapshot_id,
        parent_snapshot_id=_parent_snapshot_id(snapshot),
        timestamp_ms=timestamp_ms,
        committed_at=_committed_at(timestamp_ms),
        sequence_number=_attr_int(snapshot, "sequence_number"),
        operation=_operation(summary),
        summary=_summary_read(summary),
        is_current=False,
        refs=refs,
        protected=False,
        protected_label=None,
    )


def _extract_metadata(metadata: Any) -> tuple[str | None, list[SnapshotRead]]:
    # Metadata fields only. Do not call manifests(), scan(), or inspect().
    snapshots = getattr(metadata, "snapshots", None)
    if snapshots is None:
        snapshots = []
    if not isinstance(snapshots, (list, tuple)):
        raise RuntimeError("catalog metadata snapshots is not a list")
    refs_by_id = _refs_by_snapshot(metadata)
    rows = [
        _snapshot_row(snapshot, refs_by_id.get(_snapshot_id(snapshot), []))
        for snapshot in snapshots
    ]
    current = _decimal_field(metadata, "current_snapshot_id", "current-snapshot-id")
    return current, rows


def _load_metadata(
    source: Mapping[str, Any], schema_name: str, table_name: str
) -> tuple[str | None, list[SnapshotRead]]:
    from cryo.sources.factory import source_catalog_for

    catalog = source_catalog_for(source)
    iceberg = catalog.load_table(schema_name, table_name)
    metadata = getattr(iceberg, "metadata", None)
    if metadata is None:
        raise RuntimeError("catalog returned a table without metadata")
    return _extract_metadata(metadata)


def _catalog_failure(exc: Exception) -> HTTPException:
    reason = f"{type(exc).__name__}: {exc}"
    return HTTPException(status_code=503, detail=f"Catalog metadata read failed: {reason}"[:500])


def _protected_labels(rows: list[Any]) -> dict[str, str | None]:
    labels: dict[str, str | None] = {}
    for row in rows:
        snapshot_id = _decimal_id(row.snapshot_id)
        if snapshot_id is None:
            continue
        labels[snapshot_id] = row.label
    return labels


@router.get(
    "/tables/{table_id}/snapshots",
    response_model=SnapshotListResponse,
    dependencies=[Depends(require_viewer)],
)
def list_table_snapshots(
    table_id: uuid.UUID,
    session: SessionDep,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=_MAX_PAGE_SIZE)] = 25,
) -> SnapshotListResponse:
    table = session.get(Table, table_id)
    if table is None:
        raise HTTPException(status_code=404, detail="Table not found")
    source = session.get(Source, table.source_id)
    if source is None:
        raise HTTPException(status_code=404, detail="Source not found")

    description = f"catalog.load_table({table.schema_name}.{table.table_name})"
    source_view = {
        "name": source.name,
        "type": source.type,
        "config": dict(source.config or {}),
    }
    schema_name = table.schema_name
    table_name = table.table_name
    try:
        current_snapshot_id, rows = _run_catalog_call_bounded(
            lambda: _load_metadata(source_view, schema_name, table_name),
            description=description,
        )
    except TimeoutError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Catalog metadata read timed out: {exc}",
        ) from exc
    except HTTPException:
        raise
    except Exception as exc:
        raise _catalog_failure(exc) from exc

    labels = _protected_labels(ProtectedSnapshotRepository(session).list_for_table(table_id))
    marked: list[SnapshotRead] = []
    for row in rows:
        label = labels.get(row.snapshot_id)
        marked.append(
            row.model_copy(
                update={
                    "is_current": (
                        current_snapshot_id is not None and row.snapshot_id == current_snapshot_id
                    ),
                    "protected": row.snapshot_id in labels,
                    "protected_label": label,
                }
            )
        )
    ordered = sorted(
        marked,
        key=lambda row: (
            row.timestamp_ms is not None,
            row.timestamp_ms or 0,
            int(row.snapshot_id),
        ),
        reverse=True,
    )
    dated = [row.committed_at for row in ordered if row.committed_at is not None]
    start = (page - 1) * page_size
    return SnapshotListResponse(
        table_id=table_id,
        current_snapshot_id=current_snapshot_id,
        total=len(ordered),
        pinned_count=sum(1 for row in ordered if row.refs),
        latest_committed_at=dated[0] if dated else None,
        oldest_committed_at=dated[-1] if dated else None,
        page=page,
        page_size=page_size,
        snapshots=ordered[start : start + page_size],
    )
