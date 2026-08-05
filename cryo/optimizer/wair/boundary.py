from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class FileBounds:
    file_path: str
    size_bytes: int
    lower_bounds: Mapping[str, object]
    upper_bounds: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class QueryRange:
    column_name: str
    lower: object
    upper: object
    frequency: int = 1


@dataclass(frozen=True, slots=True)
class BoundaryFile:
    file_path: str
    size_bytes: int
    utilization_score: int
    matched_edges: int


def find_boundary_files(
    files: Sequence[FileBounds],
    query_ranges: Sequence[QueryRange],
) -> list[BoundaryFile]:
    boundaries: list[BoundaryFile] = []
    for file in files:
        utilization = 0
        matched_edges = 0
        for query_range in query_ranges:
            file_lower = file.lower_bounds.get(query_range.column_name)
            file_upper = file.upper_bounds.get(query_range.column_name)
            if file_lower is None or file_upper is None:
                continue
            lower_to_query = _compare(file_lower, query_range.lower)
            query_to_upper = _compare(query_range.lower, file_upper)
            lower_to_upper_query = _compare(file_lower, query_range.upper)
            upper_query_to_file = _compare(query_range.upper, file_upper)
            if None in {
                lower_to_query,
                query_to_upper,
                lower_to_upper_query,
                upper_query_to_file,
            }:
                continue
            overlaps_lower = lower_to_query == -1 and query_to_upper in {-1, 0}
            overlaps_upper = lower_to_upper_query in {-1, 0} and upper_query_to_file == -1
            edge_count = int(overlaps_lower) + int(overlaps_upper)
            if edge_count:
                utilization += max(1, query_range.frequency)
                matched_edges += edge_count
        if utilization:
            boundaries.append(
                BoundaryFile(
                    file_path=file.file_path,
                    size_bytes=max(0, file.size_bytes),
                    utilization_score=utilization,
                    matched_edges=matched_edges,
                )
            )
    return sorted(
        boundaries,
        key=lambda item: (-item.utilization_score, -item.matched_edges, item.file_path),
    )


def _compare(left: object, right: object) -> int | None:
    if (
        isinstance(left, int | float)
        and not isinstance(left, bool)
        and isinstance(right, int | float)
        and not isinstance(right, bool)
    ):
        return (left > right) - (left < right)
    if isinstance(left, str) and isinstance(right, str):
        return (left > right) - (left < right)
    return None
