"""WAIR boundary micro-partitions and their estimated savings.

Liu, Zhou, Yan, Zhang, "Workload-Aware Incremental Reclustering in Cloud Data Warehouses",
SIGMOD 2026 (PACMMOD 43), DOI 10.1145/3802127, arXiv:2602.23289, §3-4.2.

A micro-partition here is an Iceberg data file; its clustering-key range is the file's
manifest min/max. A file is a *boundary* file for a queried range when it partially overlaps
it (paper §3.2): fully inside or outside files need no reclustering.

Utilization ``u`` (paper §4.1) is the fraction of the file's bytes a query needs. The paper
measures it from execution statistics; Cryo has only file min/max, so it assumes values are
spread uniformly across the file's key range: ``u = |file ∩ query| / |file|``.

Savings per file and query follow Eq. 3, in CPU seconds:
``Δt(P|Q) = (1 - u) * size(P) / size_read(Q) * t_read(Q)``, where ``t_read / size_read`` is the
query's own measured CPU time per byte read, summed over every query in the window W.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from cryo.optimizer.wair.ranges import sort_key


@dataclass(frozen=True, slots=True)
class FileBounds:
    file_path: str
    size_bytes: int
    lower_bounds: Mapping[str, object]
    upper_bounds: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class QueryRange:
    """A range predicate seen ``frequency`` times in the window; None is an open side."""

    column_name: str
    lower: object
    upper: object
    frequency: int = 1
    cpu_seconds_per_byte: float | None = None


@dataclass(frozen=True, slots=True)
class BoundaryFile:
    file_path: str
    size_bytes: int
    savings_cpu_seconds: float
    savings_by_column: Mapping[str, float] = field(default_factory=dict)
    # Query endpoints (column -> raw literal values) this file straddles.
    edges: Mapping[str, tuple[object, ...]] = field(default_factory=dict)


def utilization(
    file_lower: float, file_upper: float, query_lower: float | None, query_upper: float | None
) -> float | None:
    """Fraction of the file a query reads, or None when min/max pruning skips it entirely."""
    if file_upper < file_lower:
        return None
    lo = file_lower if query_lower is None else max(file_lower, query_lower)
    hi = file_upper if query_upper is None else min(file_upper, query_upper)
    if hi < lo:
        return None
    if file_upper == file_lower:
        return 1.0
    return (hi - lo) / (file_upper - file_lower)


def find_boundary_files(
    files: Sequence[FileBounds],
    query_ranges: Sequence[QueryRange],
    *,
    default_cpu_seconds_per_byte: float | None = None,
) -> list[BoundaryFile]:
    boundaries: list[BoundaryFile] = []
    for file in files:
        by_column: dict[str, float] = {}
        edges: dict[str, list[object]] = {}
        for query in query_ranges:
            file_lo = sort_key(file.lower_bounds.get(query.column_name))
            file_hi = sort_key(file.upper_bounds.get(query.column_name))
            q_lo = sort_key(query.lower)
            q_hi = sort_key(query.upper)
            if file_lo is None or file_hi is None:
                continue
            if (query.lower is not None and q_lo is None) or (
                query.upper is not None and q_hi is None
            ):
                continue
            u = utilization(file_lo, file_hi, q_lo, q_hi)
            if u is None or u >= 1.0:
                continue
            touched = []
            if q_lo is not None and file_lo < q_lo <= file_hi:
                touched.append(query.lower)
            if q_hi is not None and file_lo <= q_hi < file_hi:
                touched.append(query.upper)
            if not touched:
                continue
            rate = query.cpu_seconds_per_byte or default_cpu_seconds_per_byte
            saving = (1.0 - u) * max(0, file.size_bytes) * (rate or 0.0) * max(1, query.frequency)
            by_column[query.column_name] = by_column.get(query.column_name, 0.0) + saving
            column_edges = edges.setdefault(query.column_name, [])
            column_edges.extend(v for v in touched if v not in column_edges)
        if edges:
            boundaries.append(
                BoundaryFile(
                    file_path=file.file_path,
                    size_bytes=max(0, file.size_bytes),
                    savings_cpu_seconds=sum(by_column.values()),
                    savings_by_column=by_column,
                    edges={column: tuple(values) for column, values in edges.items()},
                )
            )
    return sorted(boundaries, key=lambda b: (-b.savings_cpu_seconds, b.file_path))
