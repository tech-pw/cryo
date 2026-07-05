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
    _sync_embedding_vec(session, cached, vec)
    return vec


def _sync_embedding_vec(session: Session, cached: TableEmbedding, vec: tuple[float, ...]) -> None:
    """Best-effort sync to pgvector column when available."""
    from sqlalchemy import text

    try:
        vec_literal = "[" + ",".join(str(v) for v in vec) + "]"
        session.execute(
            text(
                "UPDATE cryo.table_embeddings SET embedding_vec = CAST(:vec AS vector) "
                "WHERE table_id = :tid"
            ),
            {"vec": vec_literal, "tid": cached.table_id},
        )
    except Exception:
        pass


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
    ann_results = _find_similar_ann(session, target_id, target_vec, min_similarity, limit)
    if ann_results is not None:
        return ann_results

    results: list[SimilarTable] = []
    batch_size = 250
    for batch_start in range(0, len(fleet), batch_size):
        batch = fleet[batch_start : batch_start + batch_size]
        for row in batch:
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
        session.flush()
    results.sort(key=lambda item: item.similarity, reverse=True)
    return results[:limit]


def _find_similar_ann(
    session: Session,
    target_id: str,
    target_vec: tuple[float, ...],
    min_similarity: float,
    limit: int,
) -> list[SimilarTable] | None:
    """Use pgvector ANN when embedding_vec column is populated."""
    from sqlalchemy import select, text

    from cryo.db.models import Table

    try:
        vec_literal = "[" + ",".join(str(v) for v in target_vec) + "]"
        rows = session.execute(
            text(
                """
                SELECT te.table_id,
                       1 - (te.embedding_vec <=> CAST(:vec AS vector)) AS similarity
                FROM cryo.table_embeddings te
                WHERE te.embedding_vec IS NOT NULL
                  AND te.table_id::text != :target_id
                  AND 1 - (te.embedding_vec <=> CAST(:vec AS vector)) >= :min_sim
                ORDER BY te.embedding_vec <=> CAST(:vec AS vector)
                LIMIT :lim
                """
            ),
            {"vec": vec_literal, "target_id": target_id, "min_sim": min_similarity, "lim": limit},
        ).all()
    except Exception:
        return None

    if not rows:
        return []

    table_ids = [row[0] for row in rows]
    tables = {t.id: t for t in session.scalars(select(Table).where(Table.id.in_(table_ids))).all()}
    results: list[SimilarTable] = []
    for table_id, similarity in rows:
        table = tables.get(table_id)
        if table is None:
            continue
        results.append(
            SimilarTable(
                table_id=str(table_id),
                schema_name=table.schema_name,
                table_name=table.table_name,
                similarity=float(similarity),
                shared_signals=[],
            )
        )
    return results
