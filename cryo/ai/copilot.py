"""Cryo Copilot verified questions over Postgres metadata (no live Iceberg SQL)."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from cryo.db.models import Table, TableStats

VerifiedHandler = Callable[[Mapping[str, Any], Session], dict[str, Any]]


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
