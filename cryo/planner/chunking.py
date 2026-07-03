from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass


@dataclass(frozen=True)
class ChunkedPartition:
    value: str
    size_gb: float
    small_files: int
    payload: object | None = None


def greedy_pack[T](
    candidates: Iterable[T],
    *,
    gb_budget: float,
    max_parts: int,
    max_chunks: int | None = None,
    oversize_alone: bool = True,
    size_getter: str = "size_gb",
) -> list[list[T]]:
    chunks: list[list[T]] = []
    current: list[T] = []
    current_gb = 0.0

    for candidate in candidates:
        size_gb = float(getattr(candidate, size_getter))
        if oversize_alone and size_gb > gb_budget:
            if current:
                chunks.append(current)
                if _limit_reached(chunks, max_chunks):
                    return chunks
                current = []
                current_gb = 0.0
            chunks.append([candidate])
            if _limit_reached(chunks, max_chunks):
                return chunks
            continue

        would_exceed_budget = current and current_gb + size_gb > gb_budget
        would_exceed_parts = current and len(current) >= max_parts
        if would_exceed_budget or would_exceed_parts:
            chunks.append(current)
            if _limit_reached(chunks, max_chunks):
                return chunks
            current = []
            current_gb = 0.0

        current.append(candidate)
        current_gb += size_gb

    if current and not _limit_reached(chunks, max_chunks):
        chunks.append(current)
    return chunks


def _limit_reached[T](chunks: list[list[T]], max_chunks: int | None) -> bool:
    return max_chunks is not None and len(chunks) >= max_chunks
