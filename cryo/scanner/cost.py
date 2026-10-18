from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from cryo.config import Settings
from cryo.cost.model import CostStatsInput, ReclaimBytes, project_savings
from cryo.cost.s3_pricing import S3StorageClass, calculate_monthly_storage_cost
from cryo.domain.enums import CostPrecision
from cryo.domain.models import StorageBreakdown

MB = 1024 * 1024
GB = 1024 * MB
S3_COST_PER_GB_MONTH = 0.023


@dataclass(frozen=True)
class StorageCostEstimate:
    monthly_s3_cost_usd: float
    snapshot_overhead_gb: float


@dataclass(frozen=True)
class MetadataCostAccounting:
    precision: CostPrecision
    breakdown: StorageBreakdown
    reclaim_expire_bytes: int
    reclaim_optimize_bytes: int
    reclaim_manifests_bytes: int
    small_file_bytes: int
    referenced_file_paths: dict[str, int]
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TableCostAnalysis:
    scan_at: datetime
    precision: CostPrecision
    price_per_gb_month: float
    breakdown: StorageBreakdown
    reclaim_expire_bytes: int
    reclaim_orphan_bytes: int
    reclaim_optimize_bytes: int
    reclaim_manifests_bytes: int
    current_monthly_cost_usd: float
    projected_monthly_cost_usd: float
    projected_savings_usd_month: float
    query_request_savings_usd_month: float
    reclaimable_bytes: int
    last_full_cost_scan_at: datetime | None
    referenced_file_paths: dict[str, int] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)


def estimate_monthly_s3_cost_usd(total_size_bytes: int, snapshot_overhead_bytes: int = 0) -> float:
    total_gb = (total_size_bytes + snapshot_overhead_bytes) / GB
    return round(
        calculate_monthly_storage_cost(total_gb, storage_class=S3StorageClass.STANDARD),
        2,
    )


def estimate_storage_cost(
    *,
    total_size_bytes: int,
    total_delete_bytes: int = 0,
    manifest_total_bytes: int = 0,
    total_snapshots: int = 0,
) -> StorageCostEstimate:
    snapshot_overhead_bytes = 0
    if total_snapshots > 10:
        snapshot_overhead_bytes = int(total_size_bytes * 0.3 * min(total_snapshots / 100, 1.0))
    total_storage_bytes = (
        total_size_bytes + total_delete_bytes + manifest_total_bytes + snapshot_overhead_bytes
    )
    return StorageCostEstimate(
        monthly_s3_cost_usd=estimate_monthly_s3_cost_usd(total_storage_bytes),
        snapshot_overhead_gb=round(snapshot_overhead_bytes / GB, 3),
    )


def account_metadata_cost(
    table_obj: object,
    *,
    scan_at: datetime,
    settings: Settings,
    small_file_bytes: int = 0,
    current_snapshot_id: int | None = None,
) -> MetadataCostAccounting:
    snapshots = _snapshots(table_obj)
    if len(snapshots) > settings.cost_scan_max_snapshots:
        return _heuristic_fallback(
            table_obj,
            settings=settings,
            reason="snapshots",
            snapshot_count=len(snapshots),
        )

    surviving_ids = _surviving_snapshot_ids(
        snapshots,
        scan_at=scan_at,
        retention_days=settings.default_retention_days,
        retain_last=settings.default_retain_last,
        current_snapshot_id=current_snapshot_id,
    )

    referenced_files: dict[str, dict[str, Any]] = {}
    # Unchanged manifests are carried into every later snapshot's manifest list, so a
    # manifest's files belong to every snapshot that lists it, not only the first one seen.
    manifests_by_path: dict[str, tuple[object, set[int]]] = {}
    bounded_out: str | None = None

    io = _attr(table_obj, "io", None)
    for snapshot in snapshots:
        snapshot_id = _snapshot_id(snapshot)
        if snapshot_id is None:
            continue
        try:
            manifests = list(_call_or_attr(snapshot, "manifests", io, default=()) or [])
        except Exception:
            manifests = []
        for manifest in manifests:
            manifest_path = _manifest_path(manifest)
            known = manifests_by_path.get(manifest_path)
            if known is not None:
                known[1].add(snapshot_id)
                continue
            if len(manifests_by_path) >= settings.cost_scan_max_manifests:
                bounded_out = "manifests"
                break
            manifests_by_path[manifest_path] = (manifest, {snapshot_id})
        if bounded_out:
            break

    manifest_count = len(manifests_by_path)
    manifest_bytes_total = 0
    if not bounded_out:
        for manifest, manifest_snapshot_ids in manifests_by_path.values():
            manifest_bytes_total += int(_attr(manifest, "manifest_length", 0) or 0)
            for entry in _call_manifest_entries(manifest, io):
                if _entry_status_deleted(entry):
                    continue
                data_file = _attr(entry, "data_file", entry)
                file_path = _file_path(data_file)
                if not file_path:
                    continue
                size = int(_attr(data_file, "file_size_in_bytes", 0) or 0)
                content = str(_attr(data_file, "content", "")).lower()
                is_delete = content.endswith("deletes") or content in {"1", "2"}
                record = referenced_files.setdefault(
                    file_path,
                    {
                        "size_bytes": size,
                        "is_delete": is_delete,
                        "snapshot_ids": set(),
                    },
                )
                record["snapshot_ids"].update(manifest_snapshot_ids)
                record["size_bytes"] = max(record["size_bytes"], size)

    if bounded_out:
        return _heuristic_fallback(
            table_obj,
            settings=settings,
            reason=bounded_out,
            snapshot_count=len(snapshots),
            manifest_count=manifest_count,
        )

    live_bytes = delete_bytes = dead_bytes = 0
    current_id = current_snapshot_id or _current_snapshot_id(table_obj)

    for _path, record in referenced_files.items():
        size = int(record["size_bytes"])
        refs: set[int] = record["snapshot_ids"]
        is_delete = bool(record["is_delete"])
        if current_id is not None and current_id in refs:
            if is_delete:
                delete_bytes += size
            else:
                live_bytes += size
        if not refs.intersection(surviving_ids):
            dead_bytes += size

    reclaim_expire_bytes = dead_bytes
    reclaim_optimize_bytes = delete_bytes
    reclaim_manifests_bytes = 0
    assumptions: dict[str, Any] = {"manifest_rewrite": "not_estimated"}

    breakdown = StorageBreakdown(
        live_bytes=live_bytes,
        dead_bytes=dead_bytes,
        metadata_bytes=manifest_bytes_total,
        delete_bytes=delete_bytes,
    )
    referenced_paths = {
        path: int(record["size_bytes"]) for path, record in referenced_files.items()
    }

    return MetadataCostAccounting(
        precision=CostPrecision.METADATA,
        breakdown=breakdown,
        reclaim_expire_bytes=reclaim_expire_bytes,
        reclaim_optimize_bytes=reclaim_optimize_bytes,
        reclaim_manifests_bytes=reclaim_manifests_bytes,
        small_file_bytes=small_file_bytes,
        referenced_file_paths=referenced_paths,
        raw={
            "surviving_snapshot_ids": sorted(surviving_ids),
            "snapshot_count": len(snapshots),
            "manifest_count": manifest_count,
            "assumptions": assumptions,
        },
    )


def build_table_cost_analysis(
    metadata: MetadataCostAccounting,
    *,
    scan_at: datetime,
    settings: Settings,
    stats: CostStatsInput,
    orphan_bytes: int | None = None,
    listed_bytes: int | None = None,
    last_full_cost_scan_at: datetime | None = None,
    precision: CostPrecision | None = None,
    live_s3_get_price_per_1k: float | None = None,
) -> TableCostAnalysis:
    effective_precision = precision or metadata.precision
    breakdown = StorageBreakdown(
        live_bytes=metadata.breakdown.live_bytes,
        dead_bytes=metadata.breakdown.dead_bytes,
        metadata_bytes=metadata.breakdown.metadata_bytes,
        delete_bytes=metadata.breakdown.delete_bytes,
        orphan_bytes=orphan_bytes,
        listed_bytes=listed_bytes,
    )
    reclaim = ReclaimBytes(
        reclaim_expire_bytes=metadata.reclaim_expire_bytes,
        reclaim_orphan_bytes=orphan_bytes or 0,
        reclaim_optimize_bytes=metadata.reclaim_optimize_bytes,
        reclaim_manifests_bytes=metadata.reclaim_manifests_bytes,
    )
    projection = project_savings(
        breakdown,
        reclaim,
        stats,
        settings,
        precision=effective_precision,
        assumptions=dict(metadata.raw.get("assumptions", {})),
        live_s3_get_price_per_1k=live_s3_get_price_per_1k,
    )
    return TableCostAnalysis(
        scan_at=scan_at,
        precision=effective_precision,
        price_per_gb_month=settings.cost_price_per_gb_month,
        breakdown=breakdown,
        reclaim_expire_bytes=metadata.reclaim_expire_bytes,
        reclaim_orphan_bytes=orphan_bytes or 0,
        reclaim_optimize_bytes=metadata.reclaim_optimize_bytes,
        reclaim_manifests_bytes=metadata.reclaim_manifests_bytes,
        current_monthly_cost_usd=projection.current_monthly_cost_usd,
        projected_monthly_cost_usd=projection.projected_monthly_cost_usd,
        projected_savings_usd_month=projection.projected_savings_usd_month,
        query_request_savings_usd_month=projection.query_request_savings_usd_month,
        reclaimable_bytes=projection.reclaimable_bytes,
        last_full_cost_scan_at=last_full_cost_scan_at,
        referenced_file_paths=dict(metadata.referenced_file_paths),
        raw={
            "by_operation": {op.value: usd for op, usd in projection.by_operation.items()},
            "metadata": metadata.raw,
            # projection.assumptions carries "s3_get_price_source" ("live"/"fallback_default",
            # set unconditionally in project_savings) -- without persisting it here, the UI has
            # no way to know whether query_request_savings_usd_month used a live-fetched price
            # or the hardcoded config default, defeating the whole point of §6.4's "never let a
            # fallback-derived number look as confident as a live one" requirement.
            "assumptions": projection.assumptions,
        },
    )


def _heuristic_fallback(
    table_obj: object,
    *,
    settings: Settings,
    reason: str,
    snapshot_count: int,
    manifest_count: int = 0,
) -> MetadataCostAccounting:
    """Degraded-but-safe accounting when a table exceeds cost_scan_max_snapshots/
    _manifests: walks only the CURRENT snapshot's manifests (cheap, bounded) instead
    of full snapshot history, so live_bytes/delete_bytes are still real numbers.

    ``referenced_file_paths`` must be populated from that same walk, not left empty.
    It used to be hardcoded to {} -- "no known references" -- which is fine for THIS
    function's own live/dead accounting (computed independently above) but gets
    handed to scan_orphans() downstream, which treats "not in the referenced set" as
    "orphan, safe to delete." An empty set there doesn't mean "no references exist,"
    it means "we didn't look" -- but scan_orphans can't tell the difference, so every
    listed S3 object under the table's location gets misclassified as 100% orphan.
    Confirmed live, 2026-08-06: 50 of the 54 tables with >200 snapshots (the default
    cost_scan_max_snapshots) hit this fallback and showed orphan_bytes == listed_bytes
    -- 91.95TB of false "reclaimable" storage, the single largest remaining source of
    inflated reclaimable-bytes fleet-wide, including one 24TB table that reported
    64.6TB "reclaimable" (2.7x its own size).
    """
    del settings
    current = _current_snapshot(table_obj)
    live_bytes = delete_bytes = 0
    manifest_bytes = 0
    referenced_paths: dict[str, int] = {}
    if current is not None:
        io = _attr(table_obj, "io", None)
        for manifest in _call_or_attr(current, "manifests", io, default=()) or []:
            manifest_bytes += int(_attr(manifest, "manifest_length", 0) or 0)
            for entry in _call_manifest_entries(manifest, io):
                if _entry_status_deleted(entry):
                    continue
                data_file = _attr(entry, "data_file", entry)
                size = int(_attr(data_file, "file_size_in_bytes", 0) or 0)
                content = str(_attr(data_file, "content", "")).lower()
                file_path = _file_path(data_file)
                if file_path:
                    referenced_paths[file_path] = max(referenced_paths.get(file_path, 0), size)
                if content.endswith("deletes") or content in {"1", "2"}:
                    delete_bytes += size
                else:
                    live_bytes += size
    return MetadataCostAccounting(
        precision=CostPrecision.HEURISTIC,
        breakdown=StorageBreakdown(
            live_bytes=live_bytes,
            dead_bytes=0,
            metadata_bytes=manifest_bytes,
            delete_bytes=delete_bytes,
        ),
        reclaim_expire_bytes=0,
        reclaim_optimize_bytes=delete_bytes,
        reclaim_manifests_bytes=0,
        small_file_bytes=0,
        referenced_file_paths=referenced_paths,
        raw={
            "bounded_out": reason,
            "snapshot_count": snapshot_count,
            "manifest_count": manifest_count,
        },
    )


def _surviving_snapshot_ids(
    snapshots: list[object],
    *,
    scan_at: datetime,
    retention_days: int,
    retain_last: int,
    current_snapshot_id: int | None,
) -> set[int]:
    cutoff = scan_at - timedelta(days=retention_days)
    surviving: set[int] = set()
    if current_snapshot_id is not None:
        surviving.add(current_snapshot_id)
    sorted_snapshots = sorted(snapshots, key=_snapshot_timestamp_ms)
    for snapshot in sorted_snapshots:
        snapshot_id = _snapshot_id(snapshot)
        if snapshot_id is None:
            continue
        ts = _snapshot_timestamp(snapshot)
        if ts is not None and ts >= cutoff:
            surviving.add(snapshot_id)
    for snapshot in sorted_snapshots[-retain_last:]:
        snapshot_id = _snapshot_id(snapshot)
        if snapshot_id is not None:
            surviving.add(snapshot_id)
    return surviving


def _snapshots(table_obj: object) -> list[object]:
    snapshots = _call_or_attr(table_obj, "snapshots", default=None)
    if snapshots is None:
        metadata = _attr(table_obj, "metadata", None)
        snapshots = _attr(metadata, "snapshots", []) or []
    return list(snapshots or [])


def _current_snapshot(table_obj: object) -> object | None:
    return cast(object | None, _call_or_attr(table_obj, "current_snapshot", default=None))


def _current_snapshot_id(table_obj: object) -> int | None:
    current = _current_snapshot(table_obj)
    return _snapshot_id(current) if current is not None else None


def _snapshot_id(snapshot: object | None) -> int | None:
    if snapshot is None:
        return None
    value = _attr(snapshot, "snapshot_id", None)
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _snapshot_timestamp(snapshot: object) -> datetime | None:
    ts = _attr(snapshot, "timestamp_ms", None) or _attr(snapshot, "committed_at", None)
    if ts is None:
        return None
    if isinstance(ts, datetime):
        return ts if ts.tzinfo else ts.replace(tzinfo=UTC)
    try:
        return datetime.fromtimestamp(int(ts) / 1000, tz=UTC)
    except (TypeError, ValueError, OSError):
        return None


def _snapshot_timestamp_ms(snapshot: object) -> int:
    ts = _snapshot_timestamp(snapshot)
    if ts is None:
        return 0
    return int(ts.timestamp() * 1000)


def _manifest_path(manifest: object) -> str:
    for name in ("manifest_path", "path"):
        value = _attr(manifest, name, None)
        if value:
            return str(value)
    return str(manifest)


def _file_path(data_file: object) -> str:
    for name in ("file_path", "location", "path"):
        value = _attr(data_file, name, None)
        if value:
            return str(value)
    return ""


def _entry_status_deleted(entry: object) -> bool:
    status = _attr(entry, "status", None)
    if status is None:
        return False
    status_str = str(status).upper()
    return status_str.endswith("DELETED") or status_str == "2"


def _call_manifest_entries(manifest: object, io: object) -> list[object]:
    fetch = getattr(manifest, "fetch_manifest_entry", None)
    if not callable(fetch):
        return []
    for call in (lambda: fetch(io=io, discard_deleted=True), lambda: fetch(io), fetch):
        try:
            entries = list(call() or [])
            return [entry for entry in entries if not _entry_status_deleted(entry)]
        except TypeError:
            continue
    return []


def _attr(obj: object, name: str, default: Any = None) -> Any:
    value = getattr(obj, name, default)
    return value() if callable(value) and name in {"content", "partition"} else value


def _call_or_attr(obj: object, name: str, *args: Any, default: Any = None, **kwargs: Any) -> Any:
    value = getattr(obj, name, default)
    if callable(value):
        for call in (lambda: value(*args, **kwargs), lambda: value(*args), value):
            try:
                return call()
            except TypeError:
                continue
    return value
