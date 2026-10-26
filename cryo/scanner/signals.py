from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any


def metadata_to_data_ratio(metadata_bytes: int, data_bytes: int) -> float:
    """Metadata bytes per live data byte.

    No live data with metadata present is all overhead, the worst case, not 0.0: an empty
    or fully-deleted table still carrying manifests and snapshots used to look perfect and
    took no metadata-overhead deduction (issue #191). The result is capped at 1.0 there.
    """
    if data_bytes <= 0:
        return 1.0 if metadata_bytes > 0 else 0.0
    return round(metadata_bytes / data_bytes, 4)


def detect_format_version(table_obj: object) -> int:
    metadata = getattr(table_obj, "metadata", None)
    properties = (
        getattr(table_obj, "properties", None) or getattr(metadata, "properties", None) or {}
    )
    if callable(properties):
        properties = properties()
    if not isinstance(properties, Mapping):
        properties = {}
    raw = properties.get("format-version") or properties.get("format.version")
    if raw is None:
        spec = getattr(table_obj, "spec", None)
        spec_id = getattr(spec, "spec_id", None) if spec is not None else None
        return 2 if spec_id is not None and spec_id >= 1 else 1
    return int(raw)


def is_v3_upgrade_candidate(
    format_version: int, delete_file_pct: float, *, threshold: float = 5.0
) -> bool:
    return format_version < 3 and delete_file_pct >= threshold


def detect_streaming_from_history(
    history: Sequence[Mapping[str, Any]],
    *,
    commits_per_day_threshold: float = 24.0,
    window_hours: int = 24,
) -> tuple[bool, float]:
    """Derive commits/day from consecutive snapshot-count deltas in stats history.

    Skips the inference entirely when a Cryo maintenance job completed between the two
    samples (``maintenance_job_since_previous`` on the latest row, set by
    ``stats_history_for_signals``) -- expire_snapshots/optimize/optimize_manifests each
    commit their own snapshot, so the delta is explained by Cryo's own run, not external
    write velocity.
    """
    if len(history) < 2:
        return False, 0.0

    ordered = sorted(
        history,
        key=lambda row: _as_datetime(row.get("scan_at")) or datetime.min.replace(tzinfo=UTC),
    )
    latest = ordered[-1]
    previous = ordered[-2]
    if latest.get("maintenance_job_since_previous"):
        return False, 0.0
    latest_at = _as_datetime(latest.get("scan_at"))
    previous_at = _as_datetime(previous.get("scan_at"))
    if latest_at is None or previous_at is None:
        return False, 0.0

    delta_hours = max((latest_at - previous_at).total_seconds() / 3600, 0.01)
    latest_snaps = int(latest.get("total_snapshots") or latest.get("snapshot_count") or 0)
    previous_snaps = int(previous.get("total_snapshots") or previous.get("snapshot_count") or 0)
    commits = max(latest_snaps - previous_snaps, 0)
    commits_per_day = commits / delta_hours * 24
    is_streaming = commits_per_day >= commits_per_day_threshold and delta_hours <= window_hours
    return is_streaming, round(commits_per_day, 2)


def manifest_stats(
    manifest_count: int, manifest_total_bytes: int, total_files: int
) -> dict[str, Any]:
    """Current-snapshot manifest shape, identical for the Python and Go scan paths."""
    return {
        "manifest_count": manifest_count,
        "avg_files_per_manifest": round(total_files / max(manifest_count, 1), 2),
        "manifest_total_bytes": manifest_total_bytes,
        "avg_manifest_bytes": int(manifest_total_bytes / manifest_count) if manifest_count else 0,
    }


def enrich_stats_signals(
    stats: dict[str, Any],
    *,
    metadata_bytes: int | None = None,
    data_bytes: int | None = None,
    format_version: int | None = None,
    history: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    enriched = dict(stats)
    live_bytes = int(data_bytes if data_bytes is not None else stats.get("live_bytes") or 0)
    meta_bytes = int(
        metadata_bytes if metadata_bytes is not None else stats.get("metadata_bytes") or 0
    )
    if live_bytes > 0 or meta_bytes > 0:
        enriched["metadata_to_data_ratio"] = metadata_to_data_ratio(meta_bytes, live_bytes)

    if format_version is not None:
        enriched["format_version"] = format_version
        enriched["v3_upgrade_candidate"] = is_v3_upgrade_candidate(
            format_version,
            float(stats.get("delete_file_pct") or 0),
        )

    if history:
        is_streaming, commits_per_day = detect_streaming_from_history(history)
        enriched["is_streaming"] = is_streaming
        enriched["commits_per_day"] = commits_per_day

    return enriched


def _as_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)
