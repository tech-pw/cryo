"""Precomputed table embedding cache."""

from __future__ import annotations

import hashlib
import json
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from cryo.ai.embeddings import cosine_similarity
from cryo.ai.similarity import SimilarTable, _shared_signals, build_table_embedding
from cryo.db.models import TableEmbedding


def _fingerprint(row: dict[str, Any]) -> str:
    stats = row.get("stats") or {}
    material = {
        "schema": row.get("schema_name"),
        "table": row.get("table_name"),
        "description": row.get("description"),
        "stats": {
            k: stats.get(k)
            for k in (
                "total_files",
                "small_files_count",
                "grade",
                "partition_col",
                "overall_score",
            )
        },
    }
    return hashlib.sha256(json.dumps(material, sort_keys=True).encode()).hexdigest()


def get_or_compute_embedding(session: Session, row: dict[str, Any]) -> tuple[float, ...]:
    table_id = row.get("table_id")
    if not table_id:
        return build_table_embedding(row)
    fp = _fingerprint(row)
    cached = session.get(TableEmbedding, UUID(str(table_id)))
    if cached is not None and cached.stats_fingerprint == fp and cached.embedding:
        return tuple(float(v) for v in cached.embedding)
    vec = build_table_embedding(row)
    if cached is None:
        cached = TableEmbedding(
            table_id=UUID(str(table_id)),
            stats_fingerprint=fp,
            embedding=list(vec),
        )
        session.add(cached)
    else:
        cached.stats_fingerprint = fp
        cached.embedding = list(vec)
    session.flush()
    return vec


def find_similar_tables_cached(
    session: Session,
    target: dict[str, Any],
    fleet: list[dict[str, Any]],
    *,
    min_similarity: float = 0.90,
    limit: int = 20,
) -> list[SimilarTable]:
    target_id = str(target.get("table_id") or "")
    target_vec = get_or_compute_embedding(session, target)
    results: list[SimilarTable] = []
    for row in fleet:
        row_id = str(row.get("table_id") or "")
        if row_id and row_id == target_id:
            continue
        vec = get_or_compute_embedding(session, row)
        sim = cosine_similarity(target_vec, vec)
        if sim < min_similarity:
            continue
        results.append(
            SimilarTable(
                table_id=row_id,
                schema_name=str(row.get("schema_name") or ""),
                table_name=str(row.get("table_name") or ""),
                similarity=sim,
                shared_signals=_shared_signals(target, row),
            )
        )
    results.sort(key=lambda item: item.similarity, reverse=True)
    return results[:limit]
