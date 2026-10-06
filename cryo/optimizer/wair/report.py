"""Recommend-only WAIR analysis over the scanner's file samples and the query-log window.

Abstains rather than guess: without measured query cost (CPU per byte read) there are no Eq. 3
savings, and without a measured Spark rewrite rate there is no recluster cost to weigh them
against.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field, replace

from sqlalchemy import select
from sqlalchemy.orm import Session

from cryo.config import get_settings
from cryo.db.models import Destination, Table, TableStats
from cryo.domain.enums import EngineType
from cryo.engines.spark.sql import render_wair_recluster
from cryo.optimizer.wair.boundary import BoundaryFile, FileBounds, QueryRange, find_boundary_files
from cryo.optimizer.wair.ranges import WINDOW_DAYS
from cryo.optimizer.wair.select import WairCostParams, select_boundary_files

MAX_BOUNDARY_VALUES = 50


@dataclass(frozen=True, slots=True)
class WairBoundaryReport:
    table_id: uuid.UUID
    status: str
    boundary_file_count: int
    selected_file_count: int
    proposed_rewrite_gb: float
    selected_file_paths: tuple[str, ...]
    abstention_reason: str | None = None
    window_days: int = WINDOW_DAYS
    clustering_column: str | None = None
    savings_cpu_seconds_per_window: float = 0.0
    recluster_cpu_seconds: float = 0.0
    net_cpu_seconds: float = 0.0
    aggressiveness: float = 1.0
    rewrite_gb_per_hour: float | None = None
    boundary_values: tuple[object, ...] = ()
    spark_sql: str | None = None
    model_notes: tuple[str, ...] = field(default=())


MODEL_NOTES = (
    "Utilization assumes uniform values inside each file's min/max (paper measures it).",
    "Single global clustering key (paper's baseline); hybrid DBSCAN layout not implemented.",
    "Fixed 7-day window W; the paper's adaptive window resizing is not implemented.",
    "Recluster cost is linear in bytes at the measured Spark lane rewrite rate.",
)


def analyze_wair_boundaries(
    session: Session,
    table_id: uuid.UUID,
    *,
    aggressiveness: float = 1.0,
    cost_limit_cpu_seconds: float | None = None,
    rewrite_gb_per_hour: float | None = None,
) -> WairBoundaryReport:
    table = session.get(Table, table_id)
    if table is None:
        raise ValueError(f"Table not found: {table_id}")
    stats = session.get(TableStats, table_id)
    if stats is None:
        return _abstain(table_id, "missing_table_stats")
    raw = stats.raw or {}
    files = _parse_files(raw.get("wair_file_sample"))
    if not files:
        return _abstain(table_id, "missing_wair_file_sample")
    ranges = _parse_ranges(raw.get("wair_query_ranges"))
    if not ranges:
        return _abstain(table_id, "missing_wair_query_ranges")
    if not any(r.cpu_seconds_per_byte for r in ranges):
        return _abstain(table_id, "missing_query_cost_evidence")

    if rewrite_gb_per_hour is None:
        from cryo.maintenance.coordinator import calibrated_rewrite_gb_per_hour

        rewrite_gb_per_hour = calibrated_rewrite_gb_per_hour(session)
    if not rewrite_gb_per_hour:
        return _abstain(table_id, "missing_measured_rewrite_rate")

    boundaries = find_boundary_files(
        files, ranges, default_cpu_seconds_per_byte=_pooled_rate(raw.get("wair_query_ranges"))
    )
    if not boundaries:
        return _abstain(table_id, "no_boundary_files")
    settings = get_settings()
    params = WairCostParams(
        rewrite_gb_per_hour=rewrite_gb_per_hour,
        lane_vcpus=settings.maintenance_lane_max_cpu_milli / 1000,
        aggressiveness=aggressiveness,
        cost_limit_cpu_seconds=cost_limit_cpu_seconds,
    )
    selection = select_boundary_files(boundaries, params)
    base = replace(
        _abstain(table_id, "recluster_cost_exceeds_savings"),
        boundary_file_count=len(boundaries),
        aggressiveness=aggressiveness,
        rewrite_gb_per_hour=rewrite_gb_per_hour,
        model_notes=MODEL_NOTES,
    )
    if not selection.selected:
        return base
    column = selection.clustering_column
    values = _boundary_values(selection.selected, column)
    return replace(
        base,
        status="analyzed",
        abstention_reason=None,
        selected_file_count=len(selection.selected),
        proposed_rewrite_gb=round(selection.selected_bytes / 1024**3, 6),
        selected_file_paths=tuple(item.file_path for item in selection.selected),
        clustering_column=column,
        savings_cpu_seconds_per_window=round(selection.savings_cpu_seconds, 3),
        recluster_cpu_seconds=round(selection.recluster_cpu_seconds, 3),
        net_cpu_seconds=round(selection.net_cpu_seconds, 3),
        boundary_values=values,
        spark_sql=_spark_sql(session, table, raw, column, values),
    )


def _boundary_values(files: tuple[BoundaryFile, ...], column: str | None) -> tuple[object, ...]:
    if column is None:
        return ()
    values: list[object] = []
    for file in files:
        for value in file.edges.get(column, ()):
            if value not in values:
                values.append(value)
    return tuple(values[:MAX_BOUNDARY_VALUES])


def _spark_sql(
    session: Session,
    table: Table,
    raw: Mapping[str, object],
    column: str | None,
    values: tuple[object, ...],
) -> str | None:
    if column is None or not values:
        return None
    columns = raw.get("column_quality")
    column_type = next(
        (
            str(c.get("column_type"))
            for c in (columns if isinstance(columns, list) else [])
            if isinstance(c, Mapping) and c.get("column_name") == column and c.get("column_type")
        ),
        None,
    )
    catalog = _spark_catalog(session)
    if column_type is None or catalog is None:
        return None
    return render_wair_recluster(
        catalog, f"{table.schema_name}.{table.table_name}", column, values, column_type=column_type
    )


def _spark_catalog(session: Session) -> str | None:
    """The batch pipeline's Spark catalog, resolved the same way the coordinator does."""
    wanted = get_settings().maintenance_destination_id
    if wanted:
        row = session.get(Destination, uuid.UUID(wanted))
        rows = [row] if row is not None else []
    else:
        rows = list(
            session.scalars(
                select(Destination)
                .where(
                    Destination.enabled.is_(True),
                    Destination.engine == EngineType.SPARK_EMR.value,
                )
                .limit(2)
            )
        )
    catalog = (rows[0].config or {}).get("catalog") if len(rows) == 1 else None
    return str(catalog) if catalog else None


def _parse_files(value: object) -> list[FileBounds]:
    if not isinstance(value, list):
        return []
    result: list[FileBounds] = []
    for item in value:
        if not isinstance(item, Mapping):
            continue
        path = item.get("file_path")
        size = item.get("size_bytes")
        lower = item.get("lower_bounds")
        upper = item.get("upper_bounds")
        if (
            isinstance(path, str)
            and isinstance(size, int)
            and size >= 0
            and isinstance(lower, Mapping)
            and isinstance(upper, Mapping)
        ):
            result.append(FileBounds(path, size, lower, upper))
    return result


def _parse_ranges(value: object) -> list[QueryRange]:
    if not isinstance(value, list):
        return []
    result: list[QueryRange] = []
    for item in value:
        if not isinstance(item, Mapping):
            continue
        column = item.get("column")
        lower = item.get("lower")
        upper = item.get("upper")
        frequency = item.get("frequency", 1)
        if (
            not isinstance(column, str)
            or (lower is None and upper is None)
            or not isinstance(frequency, int)
            or frequency <= 0
        ):
            continue
        cpu_ms = item.get("cpu_ms")
        input_bytes = item.get("input_bytes")
        rate = (
            float(cpu_ms) / 1000 / float(input_bytes)
            if isinstance(cpu_ms, int | float)
            and isinstance(input_bytes, int | float)
            and cpu_ms > 0
            and input_bytes > 0
            else None
        )
        result.append(QueryRange(column, lower, upper, frequency, rate))
    return result


def _pooled_rate(value: object) -> float | None:
    """Table-wide CPU seconds per byte read, for ranges whose own cost was not recorded."""
    cpu_ms = 0.0
    input_bytes = 0.0
    for item in value if isinstance(value, list) else []:
        if not isinstance(item, Mapping):
            continue
        ms, nbytes = item.get("cpu_ms"), item.get("input_bytes")
        if isinstance(ms, int | float) and isinstance(nbytes, int | float) and ms > 0 < nbytes:
            cpu_ms += float(ms)
            input_bytes += float(nbytes)
    return cpu_ms / 1000 / input_bytes if input_bytes else None


def _abstain(table_id: uuid.UUID, reason: str) -> WairBoundaryReport:
    return WairBoundaryReport(
        table_id=table_id,
        status="abstained",
        boundary_file_count=0,
        selected_file_count=0,
        proposed_rewrite_gb=0.0,
        selected_file_paths=(),
        abstention_reason=reason,
    )
