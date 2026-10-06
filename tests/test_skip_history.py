from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.dialects import postgresql

from cryo.api.routes_skip_history import (
    list_run_skip_evaluations,
    list_table_skip_evaluations,
    router,
)
from cryo.config import Settings
from cryo.db.models import Destination, MaintenanceJob, MaintenanceRun, Source, Table, TableStats
from cryo.maintenance.coordinator import BatchMaintenance, RunReport
from cryo.maintenance.funnel import decision_funnel
from cryo.maintenance.selection import AutoCompParams, fit_budget, load_candidates, rank_candidates
from cryo.maintenance.skip_history import (
    REASON_BACKOFF,
    REASON_BUDGET_CAP,
    REASON_COOLDOWN,
    REASON_EXCLUDED,
    REASON_NO_WORK,
    REASON_STALE_STATS,
    STAGE_ELIGIBLE,
    STAGE_EVALUATED,
    STAGE_NEEDS_WORK,
    STAGE_SELECTED,
    SkipEvaluation,
    insert_skip_evaluations,
    list_skip_evaluations,
    prune_skip_evaluations,
)
from cryo.queue.locks import InMemoryLockStore

NOW = datetime(2026, 9, 29, 1, 0, tzinfo=UTC)


def _stats(table_id: uuid.UUID, *, needs: bool, scan_at: datetime, size_gb: float) -> TableStats:
    return TableStats(
        table_id=table_id,
        scan_at=scan_at,
        needs_compaction=needs,
        compaction_priority="HIGH" if needs else "LOW",
        grade="D" if needs else "A",
        overall_score=40 if needs else 90,
        total_files=100,
        small_files_count=100 if needs else 0,
        total_size_gb=size_gb,
        delete_file_pct=0.0,
        query_count_30d=10,
    )


def _table(source_id: uuid.UUID, name: str, schema_name: str = "demo") -> Table:
    return Table(
        id=uuid.uuid4(),
        source_id=source_id,
        schema_name=schema_name,
        table_name=name,
        is_active=True,
    )


def test_fixture_night_records_one_row_per_table_with_the_right_reason(session) -> None:
    source = Source(
        id=uuid.uuid4(),
        name="sample",
        type="hive",
        config={},
        enabled=True,
        maintenance_schema_names=["demo"],
    )
    destination = Destination(
        id=uuid.uuid4(),
        name="spark",
        engine="spark_emr",
        enabled=True,
        config={"catalog": "sample"},
    )
    session.add_all([source, destination])
    session.flush()

    cooled = _table(source.id, "cooled")
    stale = _table(source.id, "stale")
    excluded = _table(source.id, "excluded", schema_name="other")
    backoff = _table(source.id, "backoff")
    idle = _table(source.id, "idle")
    big = _table(source.id, "big")
    small = _table(source.id, "small")
    backoff.maintenance_retry_after = NOW + timedelta(days=1)
    tables = [cooled, stale, excluded, backoff, idle, big, small]
    session.add_all(tables)
    session.flush()

    fresh = NOW - timedelta(hours=2)
    session.add_all(
        [
            _stats(cooled.id, needs=True, scan_at=fresh, size_gb=1.0),
            _stats(stale.id, needs=True, scan_at=NOW - timedelta(days=10), size_gb=1.0),
            _stats(excluded.id, needs=True, scan_at=fresh, size_gb=1.0),
            _stats(backoff.id, needs=True, scan_at=fresh, size_gb=1.0),
            _stats(idle.id, needs=False, scan_at=fresh, size_gb=1.0),
            _stats(big.id, needs=True, scan_at=fresh, size_gb=100.0),
            _stats(small.id, needs=True, scan_at=fresh, size_gb=1.0),
        ]
    )
    prior = MaintenanceRun(id=uuid.uuid4(), kind="scheduled", status="succeeded")
    session.add(prior)
    session.flush()
    session.add(
        MaintenanceJob(
            id=uuid.uuid4(),
            run_id=prior.id,
            table_id=cooled.id,
            destination_id=destination.id,
            mode="deep",
            status="succeeded",
            finished_at=NOW - timedelta(hours=1),
        )
    )
    session.commit()

    candidates = load_candidates(session, now=NOW, cooldown_hours=20, max_stats_age_days=3)
    ranked = rank_candidates(candidates, limit=50, params=AutoCompParams())
    kept, over = fit_budget(ranked, 2.0)
    assert {item.candidate.table_name for item in kept} == {"small"}
    assert {item.candidate.table_name for item in over} == {"big"}

    run_id = uuid.uuid4()
    session.add(
        MaintenanceRun(
            id=run_id,
            kind="scheduled",
            trigger="batch-cron",
            status="running",
            summary={"pipeline": "batch", "trigger": "batch-cron"},
        )
    )
    session.commit()

    rows = list(session.scalars(select(SkipEvaluation).where(SkipEvaluation.run_id == run_id)))
    by_name = {
        table.table_name: row for table in tables for row in rows if row.table_id == table.id
    }
    assert set(by_name) == {table.table_name for table in tables}
    assert len(rows) == len(tables)
    assert by_name["cooled"].reason == REASON_COOLDOWN
    assert by_name["cooled"].stage == STAGE_NEEDS_WORK
    assert by_name["stale"].reason == REASON_STALE_STATS
    assert by_name["stale"].stage == STAGE_NEEDS_WORK
    assert by_name["excluded"].reason == REASON_EXCLUDED
    assert by_name["excluded"].stage == STAGE_NEEDS_WORK
    assert by_name["backoff"].reason == REASON_BACKOFF
    assert by_name["backoff"].stage == STAGE_NEEDS_WORK
    assert by_name["idle"].reason == REASON_NO_WORK
    assert by_name["idle"].stage == STAGE_EVALUATED
    assert by_name["big"].reason == REASON_BUDGET_CAP
    assert by_name["big"].stage == STAGE_ELIGIBLE
    assert by_name["small"].reason is None
    assert by_name["small"].stage == STAGE_SELECTED
    assert all(row.run_id == run_id for row in rows)

    summary = {"pipeline": "batch", "selected": 1, "succeeded": 0, "failed": 0, "over_budget": 1}
    stages = {row["stage"]: row for row in decision_funnel(summary, history=rows)}
    assert stages["needs_work"]["reason"] != "not recorded"
    assert stages["eligible"]["reason"] != "not recorded"
    assert stages["needs_work"]["count"] == 6
    assert stages["eligible"]["count"] == 2
    assert stages["selected"]["count"] == 1
    absent = {row["stage"]: row for row in decision_funnel(summary)}
    assert absent["needs_work"]["reason"] == "not recorded"
    assert absent["eligible"]["count"] is None


def test_funnel_does_not_invent_needs_work_without_history() -> None:
    stages = {row["stage"]: row for row in decision_funnel(None, history=None)}
    assert stages["needs_work"]["reason"] == "not recorded"
    assert stages["eligible"]["reason"] == "not recorded"
    filled = decision_funnel(None, history=[])
    assert filled[0]["count"] == 0
    assert filled[0]["reason"] is None
    assert filled[1]["reason"] is None


def test_list_is_paginated_and_does_not_prune(session) -> None:
    source = Source(id=uuid.uuid4(), name="sample", type="hive", config={}, enabled=True)
    table = _table(source.id, "kept")
    run = MaintenanceRun(
        id=uuid.uuid4(),
        kind="scheduled",
        status="succeeded",
        summary={"pipeline": "batch", "trigger": "batch-cron"},
    )
    session.add_all([source, table, run])
    session.flush()
    old = SkipEvaluation(
        id=uuid.uuid4(),
        run_id=run.id,
        table_id=table.id,
        stage=STAGE_SELECTED,
        reason=None,
        evaluated_at=NOW - timedelta(days=120),
    )
    session.add(old)
    session.commit()

    listed, total = list_skip_evaluations(session, run_id=run.id, limit=10, offset=0)
    assert total == 1 and listed[0].id == old.id

    page = list_run_skip_evaluations(run.id, session, limit=1, offset=0)
    assert page.total == 1 and page.items[0].table_id == table.id
    table_page = list_table_skip_evaluations(table.id, session, limit=1, offset=0)
    assert table_page.total == 1
    with pytest.raises(HTTPException) as missing:
        list_run_skip_evaluations(uuid.uuid4(), session, limit=10, offset=0)
    assert missing.value.status_code == 404

    assert prune_skip_evaluations(session, older_than=NOW - timedelta(days=90)) == 1
    session.commit()
    remaining, total_after = list_skip_evaluations(session, table_id=table.id, limit=10, offset=0)
    assert total_after == 0 and remaining == []


def test_bulk_insert_is_one_statement_for_a_chunk() -> None:
    rows = [
        {
            "id": uuid.uuid4(),
            "run_id": uuid.uuid4(),
            "table_id": uuid.uuid4(),
            "stage": STAGE_EVALUATED,
            "reason": REASON_EXCLUDED,
            "evaluated_at": NOW,
        }
        for _ in range(3)
    ]

    class _Conn:
        dialect = postgresql.dialect()

        def __init__(self) -> None:
            self.calls: list[Any] = []

        def execute(self, stmt: Any) -> None:
            self.calls.append(stmt)

    conn = _Conn()
    insert_skip_evaluations(conn, rows)  # type: ignore[arg-type]
    assert len(conn.calls) == 1
    compiled = str(conn.calls[0].compile(dialect=postgresql.dialect()))
    assert compiled.upper().count("VALUES") == 1
    assert compiled.count("), (") == 2


def test_batch_select_records_when_the_run_row_is_inserted(session_factory) -> None:
    with session_factory() as session:
        source = Source(id=uuid.uuid4(), name="sample", type="hive", config={}, enabled=True)
        table = _table(source.id, "only")
        session.add_all([source, table])
        session.flush()
        session.add(_stats(table.id, needs=True, scan_at=NOW - timedelta(hours=1), size_gb=1.0))
        session.commit()
        table_id = table.id

    coordinator = BatchMaintenance(
        session_factory=session_factory,
        settings=Settings(maintenance_poll_seconds=5, maintenance_autocomp_budget_gbhr=100.0),
        lock_store=InMemoryLockStore(),
        clock=lambda: NOW,
    )
    report = RunReport(run_id=None, trigger="batch-cron", dry_run=False, started_at=NOW)
    chosen = coordinator._select(table_ids=None, limit=None, now=NOW, report=report)
    assert [item.candidate.table_id for item in chosen] == [table_id]

    run_id = uuid.uuid4()
    with session_factory() as session:
        session.add(
            MaintenanceRun(
                id=run_id,
                kind="scheduled",
                trigger="batch-cron",
                status="running",
                summary={"pipeline": "batch", "trigger": "batch-cron"},
            )
        )
        session.commit()
        rows = list(session.scalars(select(SkipEvaluation)))
    assert len(rows) == 1
    assert rows[0].run_id == run_id
    assert rows[0].table_id == table_id
    assert rows[0].stage == STAGE_SELECTED
    assert rows[0].reason is None


def test_router_exports_skip_history_reads() -> None:
    paths = {getattr(route, "path", None) for route in router.routes}
    assert "/api/v1/runs/{run_id}/skip-evaluations" in paths
    assert "/api/v1/tables/{table_id}/skip-evaluations" in paths
