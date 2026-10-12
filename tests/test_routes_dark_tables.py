"""Tests for cryo.api.routes_dark_tables -- the dark/zombie table detection API."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from fastapi import HTTPException
from sqlalchemy.orm import Session

from cryo.api.routes_dark_tables import dismiss_dark_table, list_dark_tables
from cryo.api.schemas import DarkTableDismissRequest
from cryo.api.security import Principal, Role
from cryo.db.models import Source, Table, TableStats
from cryo.telemetry.dark_tables import DARK_TABLE_CONFIRMATION_WINDOWS, detect_dark_table_candidates

PRINCIPAL = Principal(subject="test-operator", roles=frozenset({Role.OPERATOR}))


def _make_table(
    session: Session,
    *,
    schema_name: str = "s",
    table_name: str | None = None,
    size_gb: float = 10.0,
    query_count_30d: int = 0,
) -> Table:
    source = Source(id=uuid.uuid4(), name=f"source-{uuid.uuid4()}", type="hive", config={})
    session.add(source)
    session.flush()
    table = Table(
        id=uuid.uuid4(),
        source_id=source.id,
        schema_name=schema_name,
        table_name=table_name or f"t_{uuid.uuid4().hex[:8]}",
        is_active=True,
    )
    session.add(table)
    session.flush()
    session.add(
        TableStats(
            table_id=table.id,
            scan_at=datetime.now(UTC),
            needs_compaction=False,
            compaction_priority="LOW",
            grade="C",
            overall_score=65,
            total_files=10,
            small_files_count=1,
            total_size_gb=size_gb,
            query_count_30d=query_count_30d,
            estimated_monthly_s3_cost_usd=size_gb * 0.023,
        )
    )
    session.commit()
    return table


def _confirm(session: Session, start_day: int = 1) -> None:
    for i in range(DARK_TABLE_CONFIRMATION_WINDOWS):
        detect_dark_table_candidates(session, now=datetime(2026, 1, start_day + i, tzinfo=UTC))


def test_list_dark_tables_empty_when_no_candidates(session: Session) -> None:
    result = list_dark_tables(session)

    assert result.candidates == []
    assert result.total_candidates == 0


def test_list_dark_tables_excludes_unconfirmed_candidates(session: Session) -> None:
    _make_table(session, query_count_30d=0)
    # Only 1 check -- below DARK_TABLE_CONFIRMATION_WINDOWS.
    detect_dark_table_candidates(session, now=datetime(2026, 1, 1, tzinfo=UTC))

    result = list_dark_tables(session)
    assert result.candidates == []


def test_list_dark_tables_returns_confirmed_candidates_with_table_names(session: Session) -> None:
    table = _make_table(session, schema_name="analytics", table_name="old_report", size_gb=100.0)
    _confirm(session)

    result = list_dark_tables(session)

    assert result.total_candidates == 1
    candidate = result.candidates[0]
    assert candidate.table_id == table.id
    assert candidate.schema_name == "analytics"
    assert candidate.table_name == "old_report"
    assert candidate.consecutive_zero_query_windows == DARK_TABLE_CONFIRMATION_WINDOWS
    assert candidate.status == "candidate"
    assert result.total_size_gb == pytest.approx(100.0)


def test_list_dark_tables_sums_size_and_cost_across_candidates(session: Session) -> None:
    _make_table(session, size_gb=100.0)
    _make_table(session, size_gb=50.0)
    _confirm(session)

    result = list_dark_tables(session)

    assert result.total_candidates == 2
    assert result.total_size_gb == pytest.approx(150.0)
    assert result.total_estimated_monthly_cost_usd == pytest.approx(150.0 * 0.023)


def test_dismiss_dark_table_marks_status_and_records_who(session: Session) -> None:
    table = _make_table(session)
    _confirm(session)

    response = dismiss_dark_table(
        table.id,
        DarkTableDismissRequest(reason="quarterly compliance report"),
        session,
        PRINCIPAL,
    )

    assert response.status == "dismissed"
    assert response.dismissed_by == "test-operator"
    assert response.dismissed_reason == "quarterly compliance report"

    # Dismissed candidates drop out of the default (status="candidate") list view.
    result = list_dark_tables(session)
    assert result.candidates == []


def test_dismiss_dark_table_404s_for_unknown_table(session: Session) -> None:
    with pytest.raises(HTTPException) as exc_info:
        dismiss_dark_table(
            uuid.uuid4(),
            DarkTableDismissRequest(reason="not real"),
            session,
            PRINCIPAL,
        )
    assert exc_info.value.status_code == 404
