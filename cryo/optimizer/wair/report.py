from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass

from sqlalchemy.orm import Session

from cryo.db.models import Table, TableStats
from cryo.optimizer.wair.boundary import FileBounds, QueryRange, find_boundary_files
from cryo.optimizer.wair.select import select_boundary_files


@dataclass(frozen=True, slots=True)
class WairBoundaryReport:
    table_id: uuid.UUID
    status: str
    boundary_file_count: int
    selected_file_count: int
    proposed_rewrite_gb: float
    selected_file_paths: tuple[str, ...]
    skipped_oversize: int
    truncated: bool
    abstention_reason: str | None = None


def analyze_wair_boundaries(
    session: Session,
    table_id: uuid.UUID,
    *,
    rewrite_gb_budget: float = 10.0,
    max_files: int = 500,
) -> WairBoundaryReport:
    if session.get(Table, table_id) is None:
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

    boundaries = find_boundary_files(files, ranges)
    if not boundaries:
        return _abstain(table_id, "no_boundary_files")
    byte_budget = int(rewrite_gb_budget * 1024**3)
    selection = select_boundary_files(
        boundaries,
        rewrite_byte_budget=byte_budget,
        max_files=max_files,
    )
    if not selection.selected:
        return _abstain(table_id, "no_boundary_files_within_budget")
    return WairBoundaryReport(
        table_id=table_id,
        status="analyzed",
        boundary_file_count=len(boundaries),
        selected_file_count=len(selection.selected),
        proposed_rewrite_gb=round(selection.selected_bytes / 1024**3, 6),
        selected_file_paths=tuple(item.file_path for item in selection.selected),
        skipped_oversize=selection.skipped_oversize,
        truncated=selection.truncated,
    )


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
            isinstance(column, str)
            and lower is not None
            and upper is not None
            and isinstance(frequency, int)
            and frequency > 0
        ):
            result.append(QueryRange(column, lower, upper, frequency))
    return result


def _abstain(table_id: uuid.UUID, reason: str) -> WairBoundaryReport:
    return WairBoundaryReport(
        table_id=table_id,
        status="abstained",
        boundary_file_count=0,
        selected_file_count=0,
        proposed_rewrite_gb=0.0,
        selected_file_paths=(),
        skipped_oversize=0,
        truncated=False,
        abstention_reason=reason,
    )
