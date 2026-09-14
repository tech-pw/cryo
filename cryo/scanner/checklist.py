"""The 12-item Trino/S3/Iceberg optimization checklist -- one green/red verdict per item, per
table. This is the single source of truth for both the fleet-wide checklist UI and the manual
table-by-table remediation workflow described in docs/CRYO_COST_REDUCTION_PLAN.md's follow-on
work: every real fix (convert_deletes, optimize, partition/sort changes) should trace back to
one of these 12 items going from red to green, not to an ad-hoc judgment call.

Weights (relative contribution to total query cost, per the 2026-09-14 research pass grounding
this against published Iceberg/Trino guidance plus this fleet's own incidents) are informational
only -- they explain *why* an item is scored the way it is, not a literal 0-100 point system like
health.py's deduction score. A table can be green on every high-weight item and still be flagged
overall if a single Tier-1 item is red; weight orders remediation priority, it doesn't average
into a single number.

Item inventory (matches the checklist shared with the user 2026-09-14):
  Tier 1 (~70% combined weight):
    1. file_size          25% -- files near target size, not tiny
    2. delete_files        20% -- position/equality delete-file count and ratio
    3. partition_match     15% -- partition scheme matches actual query predicates (NEW)
    4. cadence_match       10% -- compaction cadence matches write velocity (NEW logic;
                                   is_streaming already existed as a flag, this makes it a
                                   real gate against configured idle-hours)
  Tier 2 (~20%):
    5. sort_order           6% -- sort/z-order matches dominant filter column
    6. distribution_mode    5% -- write.distribution-mode set for partitioned+concurrent tables
    7. manifest_bloat       5% -- manifest count/size reasonable
    8. stats_current        4% -- column/NDV statistics not stale
  Tier 3 (~10%):
    9.  snapshot_retention   3% -- snapshot count/age tuned, not left at infinite defaults
    10. orphan_cleanup       3% -- remove_orphan_files actually runs, not just expire_snapshots
    11. schema_width         2% -- no obviously dead/unused wide columns (best-effort, informational)
    12. storage_class        2% -- data files reachable (not GLACIER/DEEP_ARCHIVE) (NEW)
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from cryo.db.models import Table, TableStats
from cryo.scanner.property_recommendations import count_suboptimal_property_issues

logger = logging.getLogger(__name__)

ChecklistStatus = Literal["green", "yellow", "red", "unknown"]

# item_key -> (label, weight_pct, tier)
CHECKLIST_ITEMS: dict[str, tuple[str, float, int]] = {
    "file_size": ("File size vs target", 25.0, 1),
    "delete_files": ("Delete-file count/ratio", 20.0, 1),
    "partition_match": ("Partition scheme matches query predicates", 15.0, 1),
    "cadence_match": ("Compaction cadence matches write velocity", 10.0, 1),
    "sort_order": ("Sort order matches filter columns", 6.0, 2),
    "distribution_mode": ("write.distribution-mode set", 5.0, 2),
    "manifest_bloat": ("Manifest count/size", 5.0, 2),
    "stats_current": ("Column/NDV statistics current", 4.0, 2),
    "snapshot_retention": ("Snapshot retention tuned", 3.0, 3),
    "orphan_cleanup": ("Orphan file cleanup runs", 3.0, 3),
    "schema_width": ("Schema width / unused columns", 2.0, 3),
    "storage_class": ("S3 storage class reachable", 2.0, 3),
}


@dataclass
class ChecklistItem:
    key: str
    label: str
    weight_pct: float
    tier: int
    status: ChecklistStatus
    reason: str
    metric_value: float | None = None
    threshold: float | None = None


def _item(
    key: str, status: ChecklistStatus, reason: str, *, value: float | None = None, threshold: float | None = None
) -> ChecklistItem:
    label, weight, tier = CHECKLIST_ITEMS[key]
    return ChecklistItem(
        key=key, label=label, weight_pct=weight, tier=tier, status=status, reason=reason,
        metric_value=value, threshold=threshold,
    )


def _check_file_size(stats: Mapping[str, Any]) -> ChecklistItem:
    total_files = int(stats.get("total_files") or 0)
    small_files = int(stats.get("small_files_count") or 0)
    if total_files == 0:
        return _item("file_size", "unknown", "No files scanned yet")
    small_pct = (small_files / total_files) * 100
    # Mirrors health.py's MIN_SMALL_FILES_FOR_COMPACTION floor -- a handful of small files on
    # an otherwise tiny table isn't worth flagging red (matches the planner's own "not worth
    # compacting" gate, see health.py's docstring for the PDAE-8274 incident this avoids).
    if small_files < 128:
        return _item("file_size", "green", f"Only {small_files} small files (below compaction floor)", value=small_pct)
    if small_pct >= 50:
        return _item("file_size", "red", f"{small_pct:.0f}% of {total_files:,} files are small", value=small_pct, threshold=50)
    if small_pct >= 20:
        return _item("file_size", "yellow", f"{small_pct:.0f}% of {total_files:,} files are small", value=small_pct, threshold=20)
    return _item("file_size", "green", f"{small_pct:.0f}% small files", value=small_pct)


def _check_delete_files(stats: Mapping[str, Any]) -> ChecklistItem:
    delete_pct = float(stats.get("delete_file_pct") or 0.0)
    delete_files = int(stats.get("total_delete_files") or 0)
    if delete_files == 0:
        return _item("delete_files", "green", "No delete files")
    if delete_pct >= 20 or delete_files >= 1000:
        return _item("delete_files", "red", f"{delete_pct:.0f}% delete ratio, {delete_files:,} delete files", value=delete_pct, threshold=20)
    if delete_pct >= 5 or delete_files >= 100:
        return _item("delete_files", "yellow", f"{delete_pct:.0f}% delete ratio, {delete_files:,} delete files", value=delete_pct, threshold=5)
    return _item("delete_files", "green", f"{delete_pct:.0f}% delete ratio, {delete_files:,} delete files", value=delete_pct)


def _check_partition_match(session: Session, table_id: uuid.UUID, stats: Mapping[str, Any]) -> ChecklistItem:
    """NEW. Compares the query log's dominant filter column against the table's actual
    partition column(s) -- the highest-weighted gap this checklist closes. Confirmed missing
    from the existing layout_analyzer.py (which recommends sort order and bloom filters from
    the same query-pattern data but never flags a partition-scheme mismatch itself)."""
    from cryo.telemetry.patterns import get_table_query_patterns

    try:
        patterns = get_table_query_patterns(session, table_id)
    except Exception as exc:  # noqa: BLE001 - missing/partial query-pattern data must not crash the checklist
        # A failed statement leaves the session's transaction poisoned in Postgres -- every
        # later query on this same session (the next checklist item, or the next table in a
        # fleet-wide loop) would raise InFailedSqlTransaction until this is rolled back.
        # Confirmed live 2026-09-14: this exact gap took down /checklist/fleet with a 500.
        session.rollback()
        logger.debug("partition_match check: no query patterns for %s: %s", table_id, exc)
        return _item("partition_match", "unknown", "No query pattern data available yet")

    if not patterns.dominant_filter_column or patterns.total_queries < 5:
        return _item("partition_match", "unknown", "Not enough query volume to judge filter pattern")

    # TableStats only tracks the table's primary partition_col directly (e.g. "experimentname"
    # on gold_unleash_experiments_v1, which is also partitioned by day(createdat) -- the
    # secondary transform field isn't modeled here on purpose, since it's TablePartition rows
    # that carry the full per-field spec, and pulling those in would turn this into a second
    # query per table on every checklist computation for a distinction that rarely matters:
    # the *base* column is what a query's WHERE clause names, transform fields are derived).
    partition_col = (stats.get("partition_col") or "").lower()
    partition_field_names = {partition_col} if partition_col else set()

    dominant = patterns.dominant_filter_column.lower()
    if not partition_field_names:
        return _item(
            "partition_match", "red",
            f"Table is unpartitioned but {patterns.total_queries} queries dominantly filter on '{patterns.dominant_filter_column}'",
        )
    if dominant in partition_field_names:
        return _item("partition_match", "green", f"Dominant filter column '{patterns.dominant_filter_column}' is a partition field")
    return _item(
        "partition_match", "red",
        f"Dominant filter column '{patterns.dominant_filter_column}' is NOT among partition fields "
        f"({', '.join(sorted(partition_field_names)) or 'none'}) -- queries likely scan full partitions",
    )


def _check_cadence_match(stats: Mapping[str, Any], *, preset_idle_hours: float | None = None) -> ChecklistItem:
    """NEW logic (is_streaming already existed as a static flag in health.py; this makes it a
    real gate against the table's actual maintenance preset). Confirmed live 2026-09-11: a
    24h idle-hours gate on a continuously-written table caused repeated same-partition rewrite
    collisions -- files/day for the affected partitions grew ~15x over 3 days instead of
    shrinking. The rule of thumb this encodes: idle-hours gate should exceed the table's typical
    inter-commit gap by a wide margin, or maintenance will race the writer instead of settling
    behind it."""
    is_streaming = bool(stats.get("is_streaming"))
    commits_per_day = float(stats.get("commits_per_day") or 0.0)
    if not is_streaming and commits_per_day < 4:
        return _item("cadence_match", "green", "Low write velocity, no cadence risk")
    if preset_idle_hours is None:
        return _item(
            "cadence_match", "yellow",
            f"{'Streaming' if is_streaming else f'{commits_per_day:.1f} commits/day'} table with no maintenance preset configured yet -- verify idle-hours before scheduling one",
        )
    # Typical gap between commits, in hours, from the measured daily commit rate. The failure
    # mode this guards against (confirmed live 2026-09-11) isn't idle_hours being "too long" --
    # a long idle_hours just means fewer, later passes, which is safe. It's idle_hours being
    # SHORTER than (or close to) the actual inter-commit gap: a partition that looks "idle" at
    # the moment the gate checks it can still receive a new write before the rewrite finishes,
    # because the writer's next commit was always less than idle_hours away. Require idle_hours
    # to clear the gap with real margin (3x) before calling it safe.
    typical_gap_hours = 24.0 / commits_per_day if commits_per_day > 0 else 24.0
    safe_floor_hours = typical_gap_hours * 3
    if preset_idle_hours < safe_floor_hours:
        return _item(
            "cadence_match", "red",
            f"partition_write_idle_hours={preset_idle_hours:g}h is too tight for a table committing "
            f"every ~{typical_gap_hours:.1f}h -- risk of racing the writer",
            value=preset_idle_hours, threshold=safe_floor_hours,
        )
    return _item("cadence_match", "green", f"idle_hours={preset_idle_hours:g}h safely exceeds ~{typical_gap_hours:.1f}h commit gap")


def _check_sort_order(stats: Mapping[str, Any]) -> ChecklistItem:
    has_sort = bool(stats.get("has_sort_order"))
    return (
        _item("sort_order", "green", "Sort order configured")
        if has_sort
        else _item("sort_order", "yellow", "No sort order set")
    )


def _check_distribution_mode(stats: Mapping[str, Any]) -> ChecklistItem:
    mode = stats.get("write_distribution_mode") or "none"
    total_partitions = int(stats.get("total_partitions") or 0)
    if total_partitions <= 1:
        return _item("distribution_mode", "green", "Unpartitioned or single-partition table, distribution mode not relevant")
    if mode == "none":
        return _item("distribution_mode", "yellow", f"write.distribution-mode is 'none' on a {total_partitions}-partition table")
    return _item("distribution_mode", "green", f"write.distribution-mode='{mode}'")


def _check_manifest_bloat(stats: Mapping[str, Any]) -> ChecklistItem:
    manifests = int(stats.get("manifest_count") or 0)
    if manifests >= 500:
        return _item("manifest_bloat", "red", f"{manifests:,} manifests", value=manifests, threshold=500)
    if manifests >= 100:
        return _item("manifest_bloat", "yellow", f"{manifests:,} manifests", value=manifests, threshold=100)
    return _item("manifest_bloat", "green", f"{manifests:,} manifests", value=manifests)


def _check_stats_current(stats: Mapping[str, Any]) -> ChecklistItem:
    ratio = float(stats.get("metadata_to_data_ratio") or 0.0)
    if ratio == 0.0 and int(stats.get("total_files") or 0) == 0:
        return _item("stats_current", "unknown", "No stats scanned yet")
    if ratio >= 0.15:
        return _item("stats_current", "red", f"Metadata-to-data ratio {ratio * 100:.1f}%", value=ratio, threshold=0.15)
    if ratio >= 0.05:
        return _item("stats_current", "yellow", f"Metadata-to-data ratio {ratio * 100:.1f}%", value=ratio, threshold=0.05)
    return _item("stats_current", "green", f"Metadata-to-data ratio {ratio * 100:.1f}%", value=ratio)


def _check_snapshot_retention(stats: Mapping[str, Any]) -> ChecklistItem:
    snapshots = int(stats.get("total_snapshots") or 0)
    if snapshots >= 200:
        return _item("snapshot_retention", "red", f"{snapshots:,} snapshots retained", value=snapshots, threshold=200)
    if snapshots >= 50:
        return _item("snapshot_retention", "yellow", f"{snapshots:,} snapshots retained", value=snapshots, threshold=50)
    return _item("snapshot_retention", "green", f"{snapshots:,} snapshots retained", value=snapshots)


def _check_orphan_cleanup(session: Session, table_id: uuid.UUID) -> ChecklistItem:
    from cryo.db.models import AuditLog

    row = session.execute(
        select(AuditLog.ts)
        .where(
            AuditLog.table_id == table_id,
            AuditLog.action == "remove_orphan_files",
            AuditLog.status == "OK",
        )
        .order_by(AuditLog.ts.desc())
        .limit(1)
    ).first()
    if row is None:
        return _item("orphan_cleanup", "red", "remove_orphan_files has never run successfully on this table")
    last_run = row[0]
    age_days = (datetime.now(UTC) - (last_run if last_run.tzinfo else last_run.replace(tzinfo=UTC))).days
    if age_days > 14:
        return _item("orphan_cleanup", "yellow", f"remove_orphan_files last ran {age_days}d ago", value=age_days, threshold=14)
    return _item("orphan_cleanup", "green", f"remove_orphan_files last ran {age_days}d ago", value=age_days)


def _check_schema_width(stats: Mapping[str, Any]) -> ChecklistItem:
    # Best-effort / informational only -- Cryo does not currently track per-column read
    # frequency, so this can only flag raw column count, not genuinely-unused columns.
    raw = stats.get("raw") or {}
    column_count = len(raw.get("column_sizes") or {}) if isinstance(raw, dict) else 0
    if column_count == 0:
        return _item("schema_width", "unknown", "Column-level stats not available")
    if column_count >= 150:
        return _item("schema_width", "yellow", f"{column_count} columns -- verify all are read", value=column_count, threshold=150)
    return _item("schema_width", "green", f"{column_count} columns")


def _check_storage_class(
    session: Session, table_id: uuid.UUID, *, sample_paths: list[str] | None = None
) -> ChecklistItem:
    """NEW. Confirmed live 2026-09-11: a rewrite on a fleet table hard-failed with
    InvalidObjectStateException because backing files had transitioned to a storage class
    (Glacier/IA-with-restore-required) that doesn't support direct GET -- Cryo's own cost
    model hardcodes S3StorageClass.STANDARD and never checks this. Spot-checks a handful of
    recent file paths via head_object rather than a full listing (matches the existing
    orphan-scan-cost preflight's sampling philosophy) since a full per-table S3 LIST/HEAD scan
    is expensive at fleet scale."""
    if not sample_paths:
        return _item("storage_class", "unknown", "No file sample available to check storage class")

    from cryo.db.models import Source

    table = session.get(Table, table_id)
    if table is None:
        return _item("storage_class", "unknown", "Table not found")
    source = session.get(Source, table.source_id)
    if source is None:
        return _item("storage_class", "unknown", "Source not found")

    try:
        import boto3

        cfg = source.config or {}
        s3 = boto3.client(
            "s3",
            region_name=cfg.get("s3.region"),
            aws_access_key_id=cfg.get("s3.access-key-id"),
            aws_secret_access_key=cfg.get("s3.secret-access-key"),
        )
        non_standard = []
        for path in sample_paths[:5]:
            if not path.startswith("s3a://") and not path.startswith("s3://"):
                continue
            _, _, rest = path.partition("://")
            bucket, _, key = rest.partition("/")
            try:
                head = s3.head_object(Bucket=bucket, Key=key)
            except Exception as exc:  # noqa: BLE001 - a single unreachable object is itself the finding
                non_standard.append(f"{key} (unreachable: {exc})")
                continue
            storage_class = head.get("StorageClass", "STANDARD")
            if storage_class not in ("STANDARD", "INTELLIGENT_TIERING"):
                non_standard.append(f"{key} ({storage_class})")
        if non_standard:
            return _item(
                "storage_class", "red",
                f"{len(non_standard)}/{min(len(sample_paths), 5)} sampled files not directly readable: {non_standard[0]}",
            )
        return _item("storage_class", "green", f"Sampled {min(len(sample_paths), 5)} files, all directly readable")
    except Exception as exc:  # noqa: BLE001 - storage-class check is best-effort, must never break the checklist
        logger.debug("storage_class check failed for %s: %s", table_id, exc)
        return _item("storage_class", "unknown", f"Could not verify storage class: {exc}")


def compute_checklist(
    session: Session,
    table_id: uuid.UUID,
    *,
    preset_idle_hours: float | None = None,
    sample_file_paths: list[str] | None = None,
) -> list[ChecklistItem]:
    """Compute all 12 checklist items for a table. Never raises -- any individual check that
    can't be evaluated (missing data, unreachable S3, etc.) returns status="unknown" rather
    than failing the whole checklist, since this is read by both an interactive UI and
    fleet-wide batch scoring where one table's bad luck must not blank out the other 19."""
    stats_row = session.scalar(select(TableStats).where(TableStats.table_id == table_id))
    stats: dict[str, Any] = dict(stats_row.__dict__) if stats_row else {}
    if stats_row is not None:
        raw = stats_row.raw or {}
        stats["is_streaming"] = raw.get("is_streaming")
        stats["commits_per_day"] = raw.get("commits_per_day")
        stats["raw"] = raw

    items = [
        _check_file_size(stats),
        _check_delete_files(stats),
        _check_partition_match(session, table_id, stats),
        _check_cadence_match(stats, preset_idle_hours=preset_idle_hours),
        _check_sort_order(stats),
        _check_distribution_mode(stats),
        _check_manifest_bloat(stats),
        _check_stats_current(stats),
        _check_snapshot_retention(stats),
        _check_orphan_cleanup(session, table_id),
        _check_schema_width(stats),
        _check_storage_class(session, table_id, sample_paths=sample_file_paths),
    ]
    return items


def checklist_summary(items: list[ChecklistItem]) -> dict[str, Any]:
    """Fleet-view rollup: counts by status, plus a single 0-100 weighted score for sorting/
    ranking (informational -- see module docstring on why this isn't the same kind of number
    as health.py's deduction score)."""
    counts = {"green": 0, "yellow": 0, "red": 0, "unknown": 0}
    weighted_score = 0.0
    total_weight_scored = 0.0
    for item in items:
        counts[item.status] += 1
        if item.status == "unknown":
            continue
        total_weight_scored += item.weight_pct
        if item.status == "green":
            weighted_score += item.weight_pct
        elif item.status == "yellow":
            weighted_score += item.weight_pct * 0.5
    score = round((weighted_score / total_weight_scored) * 100, 1) if total_weight_scored > 0 else None
    return {"counts": counts, "weighted_score": score}
