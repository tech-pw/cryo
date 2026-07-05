"""Fleet-wide table/column similarity search via metadata embeddings."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from cryo.ai.embeddings import cosine_similarity, embed_table


@dataclass(frozen=True)
class SimilarTable:
    table_id: str
    schema_name: str
    table_name: str
    similarity: float
    shared_signals: list[str]


def _stats_summary(stats: dict[str, Any] | None) -> str:
    if not stats:
        return ""
    parts = [
        f"files={stats.get('total_files', 0)}",
        f"small={stats.get('small_files_count', 0)}",
        f"grade={stats.get('grade', '')}",
        f"partition={stats.get('partition_col', '')}",
    ]
    return " ".join(str(p) for p in parts if p)


def build_table_embedding(row: dict[str, Any]) -> tuple[float, ...]:
    return embed_table(
        str(row.get("schema_name") or ""),
        str(row.get("table_name") or ""),
        description=str(row.get("description") or ""),
        stats_summary=_stats_summary(row.get("stats")),
    )


def find_similar_tables(
    target: dict[str, Any],
    fleet: Sequence[dict[str, Any]],
    *,
    min_similarity: float = 0.90,
    limit: int = 20,
) -> list[SimilarTable]:
    """Return fleet tables with cosine similarity >= min_similarity to target."""
    target_id = str(target.get("table_id") or "")
    target_vec = build_table_embedding(target)
    results: list[SimilarTable] = []

    for row in fleet:
        row_id = str(row.get("table_id") or "")
        if row_id and row_id == target_id:
            continue
        vec = build_table_embedding(row)
        sim = cosine_similarity(target_vec, vec)
        if sim < min_similarity:
            continue
        signals = _shared_signals(target, row)
        results.append(
            SimilarTable(
                table_id=row_id,
                schema_name=str(row.get("schema_name") or ""),
                table_name=str(row.get("table_name") or ""),
                similarity=sim,
                shared_signals=signals,
            )
        )

    results.sort(key=lambda item: item.similarity, reverse=True)
    return results[:limit]


def _shared_signals(left: dict[str, Any], right: dict[str, Any]) -> list[str]:
    signals: list[str] = []
    left_stats = left.get("stats") or {}
    right_stats = right.get("stats") or {}
    if left_stats.get("partition_col") and left_stats.get("partition_col") == right_stats.get(
        "partition_col"
    ):
        signals.append("same_partition_column")
    left_grade = left_stats.get("grade")
    right_grade = right_stats.get("grade")
    if left_grade and left_grade == right_grade:
        signals.append(f"same_grade_{left_grade}")
    left_name = str(left.get("table_name") or "").lower()
    right_name = str(right.get("table_name") or "").lower()
    if left_name and right_name and left_name.split("_")[0] == right_name.split("_")[0]:
        signals.append("name_prefix_match")
    return signals
