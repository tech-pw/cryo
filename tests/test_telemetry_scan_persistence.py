"""#255: layout recommendations survive JSON-null query logs and one failing table;
#256: overlapping scans of one table no longer collide on table_columns, and a persist
failure is no longer swallowed by the benefit-score update."""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest
from sqlalchemy import func, select, text

from cryo.db.models import QueryLog, Source, Table, TableColumn, TableStats
from cryo.scanner.analyze import analyze_table
from cryo.scanner.persistence import persist_table_analysis
from tests.test_scanner import MB, FakeDataFile, FakeSnapshotMeta, FakeTable

SCAN_AT = datetime(2026, 10, 26, 6, 0, tzinfo=UTC)


def _analysis(table_name: str, columns: tuple[str, ...] = ("id", "ts")) -> Any:
    analysis = analyze_table(
        FakeTable(
            files=[FakeDataFile(10 * MB, 10, ("2026-10-25",))],
            snapshots=[FakeSnapshotMeta(SCAN_AT)],
        ),
        "demo",
        table_name,
        scan_at=SCAN_AT,
    )
    raw = dict(analysis.raw or {})
    raw["column_quality"] = [{"column_name": c, "column_type": "string"} for c in columns]
    return replace(analysis, raw=raw)


# --- #255: one failing table no longer aborts the rest ---------------------------------------


def test_layout_refresh_rolls_back_after_a_failed_table(monkeypatch: pytest.MonkeyPatch) -> None:
    import cryo.optimizer.layout_analyzer as layout_analyzer
    import cryo.scheduler.service as service

    first, second = uuid.uuid4(), uuid.uuid4()
    session = MagicMock()
    session.__enter__.return_value = session
    session.execute.return_value.scalars.return_value.all.return_value = [first, second]
    monkeypatch.setattr(service, "SessionLocal", lambda: session)
    calls: list[uuid.UUID] = []

    def fake_generate(sess: Any, table_id: uuid.UUID) -> None:
        calls.append(table_id)
        if table_id == first:
            raise RuntimeError("cannot get array length of a scalar")

    monkeypatch.setattr(layout_analyzer, "generate_layout_recommendation", fake_generate)
    svc = service.SchedulerService.__new__(service.SchedulerService)

    svc._generate_layout_recommendations(
        SimpleNamespace(layout_recommendation_max_tables_per_tick=10)
    )

    assert calls == [first, second]
    session.rollback.assert_called_once()


# --- #256: the benefit-score update can't poison the scan ----------------------------------


def test_benefit_score_failure_does_not_lose_the_scan(
    session, monkeypatch: pytest.MonkeyPatch
) -> None:
    import cryo.planner.benefit_score as benefit_score
    from cryo.config import get_settings

    source = Source(id=uuid.uuid4(), name=f"src-{uuid.uuid4()}", type="rest", config={})
    table = Table(id=uuid.uuid4(), source_id=source.id, schema_name="demo", table_name="events")
    session.add_all([source, table])
    session.flush()
    monkeypatch.setattr(get_settings(), "compute_statistics_enabled", True, raising=False)

    def boom(sess: Any, table_id: uuid.UUID) -> int:
        sess.execute(text("SELECT * FROM no_such_table"))
        return 0

    monkeypatch.setattr(benefit_score, "update_partition_benefit_scores", boom)

    persist_table_analysis(session, source_id=source.id, table=table, analysis=_analysis("events"))
    session.commit()

    assert session.get(TableStats, table.id) is not None
    names = session.scalars(
        select(TableColumn.column_name).where(TableColumn.table_id == table.id)
    ).all()
    assert sorted(names) == ["id", "ts"]


# --- PostgreSQL-only: JSON null and the concurrent persist ----------------------------------


def _pg_session_factory() -> Any:
    from cryo.db.session import SessionLocal

    try:
        with SessionLocal() as probe:
            if probe.get_bind().dialect.name != "postgresql":
                return None
            probe.execute(text("SELECT 1 FROM cryo.query_logs LIMIT 1"))
    except Exception:
        return None
    return SessionLocal


_PG = _pg_session_factory()
requires_pg = pytest.mark.skipif(_PG is None, reason="requires a migrated PostgreSQL")


@pytest.fixture()
def pg_source() -> Any:
    assert _PG is not None
    source_id = uuid.uuid4()
    with _PG() as s:
        s.add(Source(id=source_id, name=f"test-src-{source_id.hex[:8]}", type="rest", config={}))
        s.commit()
    yield source_id
    with _PG() as s:
        children = s.scalars(
            text(
                "SELECT table_name FROM information_schema.columns WHERE table_schema = 'cryo' "
                "AND column_name = 'table_id' AND table_name <> 'tables'"
            )
        ).all()
        for child in children:
            s.execute(
                text(
                    f"DELETE FROM cryo.{child} WHERE table_id IN "
                    "(SELECT id FROM cryo.tables WHERE source_id = :s)"
                ),
                {"s": source_id},
            )
        s.execute(text("DELETE FROM cryo.tables WHERE source_id = :s"), {"s": source_id})
        s.execute(text("DELETE FROM cryo.sources WHERE id = :s"), {"s": source_id})
        s.commit()


@requires_pg
def test_json_null_query_logs_do_not_break_the_pattern_summary(pg_source: uuid.UUID) -> None:
    from cryo.telemetry.patterns import get_table_query_patterns

    assert _PG is not None
    with _PG() as s:
        table = Table(id=uuid.uuid4(), source_id=pg_source, schema_name="demo", table_name="t")
        s.add(table)
        s.flush()
        # A new row written with None is SQL NULL now.
        s.add(
            QueryLog(
                source_id=pg_source,
                table_id=table.id,
                query_id="q-new",
                engine="trino",
                filter_columns=None,
                executed_at=SCAN_AT,
            )
        )
        s.flush()
        # A legacy row holding the JSON scalar null, as the curator used to write.
        s.execute(
            text(
                "INSERT INTO cryo.query_logs (source_id, table_id, query_id, engine, "
                "filter_columns, executed_at) VALUES (:s, :t, 'q-old', 'trino', "
                "'null'::jsonb, :at)"
            ),
            {"s": pg_source, "t": table.id, "at": SCAN_AT},
        )
        s.add(
            QueryLog(
                source_id=pg_source,
                table_id=table.id,
                query_id="q-multi",
                engine="trino",
                filter_columns=[{"column": "a"}, {"column": "b"}],
                executed_at=SCAN_AT,
            )
        )
        s.commit()

        kinds = (
            s.execute(
                select(func.jsonb_typeof(QueryLog.filter_columns))
                .where(QueryLog.table_id == table.id)
                .order_by(QueryLog.query_id)
            )
            .scalars()
            .all()
        )
        assert kinds == ["array", None, "null"]  # q-multi, q-new, q-old

        summary = get_table_query_patterns(s, table.id)
        assert summary.multi_column_filter_ratio == pytest.approx(1 / 3)


@requires_pg
def test_overlapping_persists_of_one_table_do_not_collide(
    pg_source: uuid.UUID, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cryo.config import get_settings

    assert _PG is not None
    # update_partition_benefit_scores commits on its own; keep the whole persist in one open
    # transaction, as with the scanner's caller-side commit.
    monkeypatch.setattr(get_settings(), "compute_statistics_enabled", False, raising=False)
    with _PG() as s:
        # Location already current, as on a routine rescan: no UPDATE of the tables row, so
        # nothing but the persist lock serializes the two scans.
        table = Table(
            id=uuid.uuid4(),
            source_id=pg_source,
            schema_name="demo",
            table_name="hot",
            location=(_analysis("hot").raw or {}).get("location"),
        )
        s.add(table)
        s.commit()
        table_id = table.id

    errors: list[BaseException] = []
    first_holding = threading.Event()

    def first_scan() -> None:
        try:
            with _PG() as s:
                row = s.get(Table, table_id)
                persist_table_analysis(s, source_id=pg_source, table=row, analysis=_analysis("hot"))
                first_holding.set()
                time.sleep(1.0)  # still uncommitted while the second scan starts
                s.commit()
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)
            first_holding.set()

    def second_scan() -> None:
        first_holding.wait(10)
        try:
            with _PG() as s:
                row = s.get(Table, table_id)
                persist_table_analysis(
                    s, source_id=pg_source, table=row, analysis=_analysis("hot", ("id", "v2"))
                )
                s.commit()
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=first_scan), threading.Thread(target=second_scan)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)

    assert errors == []
    assert first_holding.is_set()
    with _PG() as s:
        names = s.scalars(
            select(TableColumn.column_name).where(TableColumn.table_id == table_id)
        ).all()
    assert sorted(names) == ["id", "v2"]  # the later scan's columns, cleanly replaced
