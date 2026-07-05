"""Cryo Copilot verified questions over Postgres metadata (no live Iceberg SQL)."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from cryo.ai.embeddings import cosine_similarity, embed_text
from cryo.db.models import Table, TableStats

VerifiedHandler = Callable[[Mapping[str, Any], Session], dict[str, Any]]

# Semantic index over verified questions + table/column descriptions for embedding fallback.
_COPILOT_INDEX: list[tuple[str, str, str]] = []


def _ensure_copilot_index() -> list[tuple[str, str, str]]:
    global _COPILOT_INDEX
    if _COPILOT_INDEX:
        return _COPILOT_INDEX
    for qid, entry in VERIFIED_QUESTIONS.items():
        _COPILOT_INDEX.append((qid, entry["prompt"], "verified_question"))
    _COPILOT_INDEX.extend(
        [
            ("worst_tables", "unhealthy tables low score grade health problems", "topic"),
            ("compaction_backlog", "compaction backlog small files rewrite optimize", "topic"),
            ("cost_savings", "savings cost storage s3 monthly projected", "topic"),
            ("anomalies", "anomaly spike drift unusual metrics alerts", "topic"),
            ("pii_columns", "pii sensitive personal data classification columns", "topic"),
        ]
    )
    return _COPILOT_INDEX


def answer_verified_question(
    question_id: str,
    context: Mapping[str, Any],
    session: Session,
) -> dict[str, Any]:
    entry = VERIFIED_QUESTIONS.get(question_id)
    if entry is None:
        raise KeyError(f"Unknown verified question: {question_id}")
    result: dict[str, Any] = entry["handler"](context, session)
    return result


def answer_question(
    question: str,
    session: Session,
    *,
    context: Mapping[str, Any] | None = None,
    min_similarity: float = 0.55,
) -> dict[str, Any]:
    """Answer a natural-language question via verified library or embedding fallback."""
    normalized = question.strip().lower()
    for qid, entry in VERIFIED_QUESTIONS.items():
        if normalized == entry["prompt"].lower() or normalized == qid.replace("_", " "):
            return {
                "source": "verified",
                "question_id": qid,
                "result": answer_verified_question(qid, context or {}, session),
            }

    match = search_copilot(question, session, min_similarity=min_similarity)
    if match is None:
        return {
            "source": "none",
            "answer": (
                "No matching verified question found. "
                "Try rephrasing or pick from GET /ai/copilot/questions."
            ),
            "matches": [],
        }

    qid = match["question_id"]
    if qid in VERIFIED_QUESTIONS:
        return {
            "source": "embedding_fallback",
            "similarity": match["similarity"],
            "question_id": qid,
            "matched_text": match["text"],
            "result": answer_verified_question(qid, context or {}, session),
        }

    return {
        "source": "embedding_fallback",
        "similarity": match["similarity"],
        "question_id": qid,
        "matched_text": match["text"],
        "answer": f"Closest topic: {match['text']}. Use verified question '{qid}' if available.",
    }


def search_copilot(
    query: str,
    session: Session,
    *,
    min_similarity: float = 0.55,
    limit: int = 5,
) -> dict[str, Any] | None:
    """Embedding search over verified questions + indexed table descriptions."""
    query_vec = embed_text(query)
    candidates: list[tuple[float, str, str, str]] = []

    for qid, text, kind in _ensure_copilot_index():
        score = cosine_similarity(query_vec, embed_text(text))
        if score >= min_similarity:
            candidates.append((score, qid, text, kind))

    # Index active table names + grades as searchable metadata (no live Iceberg SQL).
    rows = session.execute(
        select(Table.schema_name, Table.table_name, TableStats.grade, TableStats.overall_score)
        .join(TableStats, TableStats.table_id == Table.id)
        .where(Table.is_active.is_(True))
        .limit(500)
    )
    for schema, name, grade, score in rows:
        desc = f"{schema}.{name} grade {grade} score {score}"
        sim = cosine_similarity(query_vec, embed_text(desc))
        if sim >= min_similarity:
            candidates.append((sim, "worst_tables", desc, "table_metadata"))

    if not candidates:
        return None

    candidates.sort(key=lambda item: item[0], reverse=True)
    best = candidates[0]
    return {
        "similarity": best[0],
        "question_id": best[1],
        "text": best[2],
        "kind": best[3],
        "alternatives": [
            {"similarity": s, "question_id": q, "text": t, "kind": k}
            for s, q, t, k in candidates[1:limit]
        ],
    }


def list_verified_questions() -> list[dict[str, str]]:
    return [{"id": key, "prompt": value["prompt"]} for key, value in VERIFIED_QUESTIONS.items()]


def _worst_tables(_context: Mapping[str, Any], session: Session) -> dict[str, Any]:
    rows = session.execute(
        select(Table, TableStats)
        .join(TableStats, TableStats.table_id == Table.id)
        .order_by(TableStats.overall_score.asc())
        .limit(10)
    )
    return {
        "answer": "Top unhealthy tables by lowest overall score.",
        "tables": [
            {
                "schema_name": table.schema_name,
                "table_name": table.table_name,
                "grade": stats.grade,
                "overall_score": stats.overall_score,
            }
            for table, stats in rows
        ],
    }


def _compaction_backlog(_context: Mapping[str, Any], session: Session) -> dict[str, Any]:
    rows = session.execute(
        select(Table.schema_name, Table.table_name, TableStats.small_files_count, TableStats.grade)
        .join(TableStats, TableStats.table_id == Table.id)
        .where(TableStats.needs_compaction.is_(True))
        .order_by(TableStats.small_files_count.desc())
        .limit(20)
    )
    return {
        "answer": "Tables flagged needs_compaction ordered by small file count.",
        "tables": [
            {
                "schema_name": schema,
                "table_name": name,
                "small_files_count": small_files,
                "grade": grade,
            }
            for schema, name, small_files, grade in rows
        ],
    }


VERIFIED_QUESTIONS: dict[str, dict[str, Any]] = {
    "worst_tables": {
        "prompt": "Which tables are the unhealthiest right now?",
        "handler": _worst_tables,
    },
    "compaction_backlog": {
        "prompt": "Which tables have the largest compaction backlog?",
        "handler": _compaction_backlog,
    },
}


def build_table_fleet_rows(session: Session) -> list[dict[str, Any]]:
    """Build fleet metadata rows for similarity search."""
    rows = session.execute(
        select(Table, TableStats)
        .join(TableStats, TableStats.table_id == Table.id)
        .where(Table.is_active.is_(True))
    )
    fleet: list[dict[str, Any]] = []
    for table, stats in rows:
        fleet.append(
            {
                "table_id": str(table.id),
                "schema_name": table.schema_name,
                "table_name": table.table_name,
                "stats": {
                    "total_files": stats.total_files,
                    "small_files_count": stats.small_files_count,
                    "grade": stats.grade,
                    "partition_col": stats.partition_col,
                    "overall_score": stats.overall_score,
                },
            }
        )
    return fleet
