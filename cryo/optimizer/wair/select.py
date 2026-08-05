from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from cryo.optimizer.wair.boundary import BoundaryFile


@dataclass(frozen=True, slots=True)
class BoundarySelection:
    selected: tuple[BoundaryFile, ...]
    selected_bytes: int
    skipped_oversize: int
    truncated: bool


def select_boundary_files(
    boundaries: Sequence[BoundaryFile],
    *,
    rewrite_byte_budget: int,
    max_files: int = 500,
) -> BoundarySelection:
    if rewrite_byte_budget < 1:
        raise ValueError("WAIR rewrite byte budget must be positive")
    if max_files < 1:
        raise ValueError("WAIR max files must be positive")

    selected: list[BoundaryFile] = []
    selected_bytes = 0
    skipped_oversize = 0
    ranked = sorted(
        boundaries,
        key=lambda item: (
            -item.utilization_score,
            -item.matched_edges,
            item.size_bytes,
            item.file_path,
        ),
    )
    for boundary in ranked:
        if boundary.size_bytes > rewrite_byte_budget:
            skipped_oversize += 1
            continue
        if len(selected) >= max_files:
            break
        if selected_bytes + boundary.size_bytes > rewrite_byte_budget:
            continue
        selected.append(boundary)
        selected_bytes += boundary.size_bytes

    return BoundarySelection(
        selected=tuple(selected),
        selected_bytes=selected_bytes,
        skipped_oversize=skipped_oversize,
        truncated=len(selected) < len(boundaries) - skipped_oversize,
    )
