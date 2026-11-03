"""#330: opt-in hard purge of tables removed from the catalog."""

from __future__ import annotations

import importlib
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

import cryo
from cryo.config import Settings, get_settings
from cryo.db.base import Base
from cryo.db.models import (
    AuditLog,
    Destination,
    MaintenanceIntent,
    MaintenanceJob,
    MaintenanceRun,
    QueryLog,
    QueryPattern,
    RunFailureDiagnosis,
    Source,
    Table,
    TableStats,
)
from cryo.db.repositories.removed_table_purge import (
    TABLE_PURGED_ACTION,
    purge_removed_tables,
)

NOW = datetime(2026, 11, 3, 3, 0, tzinfo=UTC)
RETENTION = 30
OLD = NOW - timedelta(days=RETENTION + 5)
RECENT = NOW - timedelta(days=RETENTION - 5)


@pytest.fixture()
def fk_session(session: Session) -> Session:
    # SQLite leaves foreign keys off unless asked; Postgres always enforces them.
    session.execute(text("PRAGMA foreign_keys=ON"))
    session.commit()
    assert session.execute(text("PRAGMA foreign_keys")).scalar() == 1
    return session


@dataclass(frozen=True)
class _Fleet:
    source_id: uuid.UUID
    destination_id: uuid.UUID
    run_id: uuid.UUID


def _fleet(session: Session) -> _Fleet:
    fleet = _Fleet(uuid.uuid4(), uuid.uuid4(), uuid.uuid4())
    session.add(Source(id=fleet.source_id, name="lake", type="rest", config={}))
    session.add(Destination(id=fleet.destination_id, name="trino", engine="trino", config={}))
    session.add(MaintenanceRun(id=fleet.run_id, kind="maintenance", status="succeeded"))
    session.flush()
    return fleet


def _table(session: Session, fleet: _Fleet, name: str, removed_at: datetime | None) -> Table:
    table = Table(
        id=uuid.uuid4(),
        source_id=fleet.source_id,
        schema_name="sales",
        table_name=name,
        is_active=removed_at is None,
        removed_from_catalog_at=removed_at,
        removed_reason="not_found_in_catalog: lake/sales" if removed_at else None,
    )
    session.add(table)
    session.flush()
    return table


def _job(session: Session, fleet: _Fleet, table: Table, status: str) -> MaintenanceJob:
    job = MaintenanceJob(
        id=uuid.uuid4(),
        run_id=fleet.run_id,
        table_id=table.id,
        destination_id=fleet.destination_id,
        mode="standard",
        status=status,
    )
    session.add(job)
    session.flush()
    return job


def _with_history(session: Session, fleet: _Fleet, table: Table) -> tuple[uuid.UUID, int]:
    """Stats, a finished job, query rows, a diagnosis and audit rows pointing at the table."""
    session.add(
        TableStats(
            table_id=table.id,
            scan_at=OLD,
            needs_compaction=False,
            compaction_priority=0,
            grade="A",
            overall_score=100,
            total_files=1,
            small_files_count=0,
            total_size_gb=1,
        )
    )
    job = _job(session, fleet, table, "succeeded")
    session.add(
        QueryLog(
            source_id=fleet.source_id,
            table_id=table.id,
            query_id=f"q-{table.table_name}",
            engine="trino",
            executed_at=OLD,
        )
    )
    session.add(QueryPattern(table_id=table.id, column_name="day"))
    session.add(
        RunFailureDiagnosis(
            id=uuid.uuid4(),
            run_id=fleet.run_id,
            job_id=job.id,
            table_id=table.id,
            diagnosis_class="other",
            evidence=[],
        )
    )
    audit = AuditLog(
        table_id=table.id,
        job_id=job.id,
        action="optimize",
        status="OK",
        detail=f"table={table.table_name}",
    )
    session.add(audit)
    session.flush()
    return job.id, audit.id


def _count(session: Session, model: type[Base], **where: object) -> int:
    stmt = select(func.count()).select_from(model)
    for key, value in where.items():
        stmt = stmt.where(getattr(model, key) == value)
    return int(session.scalar(stmt) or 0)


def test_purges_only_tables_removed_before_the_window_and_keeps_audit(
    fk_session: Session,
) -> None:
    session = fk_session
    fleet = _fleet(session)
    old = _table(session, fleet, "old", OLD)
    recent = _table(session, fleet, "recent", RECENT)
    live = _table(session, fleet, "live", None)
    job_id, audit_id = _with_history(session, fleet, old)
    _with_history(session, fleet, recent)
    _with_history(session, fleet, live)
    session.commit()
    old_id = old.id

    result = purge_removed_tables(session, RETENTION, now=NOW, actor="test")
    assert (result.purged, result.failed, result.held, result.remaining) == (1, 0, 0, 0)

    session.expire_all()
    assert session.get(Table, old_id) is None
    assert session.get(Table, recent.id) is not None
    assert session.get(Table, live.id) is not None
    assert _count(session, TableStats, table_id=old_id) == 0
    assert _count(session, MaintenanceJob, table_id=old_id) == 0
    assert _count(session, QueryPattern, table_id=old_id) == 0
    assert session.get(MaintenanceJob, job_id) is None

    kept = session.get(AuditLog, audit_id)
    assert kept is not None and kept.table_id is None and kept.job_id is None
    assert _count(session, QueryLog, query_id="q-old") == 1
    assert session.scalar(select(QueryLog.table_id).where(QueryLog.query_id == "q-old")) is None
    diagnoses = session.scalars(select(RunFailureDiagnosis)).all()
    assert len(diagnoses) == 3
    assert sum(1 for row in diagnoses if row.table_id is None and row.job_id is None) == 1

    (purged_row,) = session.scalars(
        select(AuditLog).where(AuditLog.action == TABLE_PURGED_ACTION)
    ).all()
    assert purged_row.actor == "test"
    assert purged_row.table_id is None
    assert "table=lake.sales.old" in (purged_row.detail or "")
    assert f"table_id={old_id}" in (purged_row.detail or "")
    assert f"retention_days={RETENTION}" in (purged_row.detail or "")


@pytest.mark.parametrize("status", ["queued", "admitted", "running", "deferred"])
def test_a_table_with_an_active_job_is_held(fk_session: Session, status: str) -> None:
    session = fk_session
    fleet = _fleet(session)
    held = _table(session, fleet, "held", OLD)
    _job(session, fleet, held, status)
    session.commit()

    result = purge_removed_tables(session, RETENTION, now=NOW)
    assert (result.purged, result.held, result.remaining) == (0, 1, 0)
    assert session.get(Table, held.id) is not None


def test_a_table_with_an_open_intent_is_held(fk_session: Session) -> None:
    session = fk_session
    fleet = _fleet(session)
    held = _table(session, fleet, "held", OLD)
    job = _job(session, fleet, held, "succeeded")
    session.add(
        MaintenanceIntent(
            job_id=job.id,
            version=1,
            status="reconcile_required",
            actor_type="system",
            actor_id="worker",
            source_id=fleet.source_id,
            table_id=held.id,
            table_ref="lake.sales.held",
            destination_id=fleet.destination_id,
            operation_scope=["optimize"],
            canonical_plan={},
            plan_hash="h",
            idempotency_key="k",
            fencing_epoch=1,
            budget={},
            capability_requirements=[],
            containment_action="none",
            expires_at=NOW + timedelta(days=1),
        )
    )
    session.commit()

    assert purge_removed_tables(session, RETENTION, now=NOW).held == 1
    assert session.get(Table, held.id) is not None


def test_a_restored_table_is_never_purged(fk_session: Session) -> None:
    session = fk_session
    fleet = _fleet(session)
    table = _table(session, fleet, "back", OLD)
    table.is_active = True
    session.commit()
    assert purge_removed_tables(session, RETENTION, now=NOW).purged == 0
    assert session.get(Table, table.id) is not None


def test_dry_run_deletes_nothing_and_max_tables_bounds_a_tick(fk_session: Session) -> None:
    session = fk_session
    fleet = _fleet(session)
    for index in range(3):
        _table(session, fleet, f"gone_{index}", OLD - timedelta(hours=index))
    session.commit()

    dry = purge_removed_tables(session, RETENTION, now=NOW, dry_run=True)
    assert (dry.purged, dry.remaining) == (0, 3)
    assert _count(session, Table) == 3
    assert _count(session, AuditLog, action=TABLE_PURGED_ACTION) == 0

    first = purge_removed_tables(session, RETENTION, now=NOW, max_tables=2)
    assert (first.purged, first.remaining) == (2, 1)
    # Oldest tombstones go first.
    assert session.scalars(select(Table.table_name)).all() == ["gone_0"]


def test_retention_must_be_positive(session: Session) -> None:
    with pytest.raises(ValueError):
        purge_removed_tables(session, 0)


def test_every_foreign_key_a_purge_reaches_has_an_ondelete_action() -> None:
    """A new table referencing tables/jobs without ON DELETE would make every purge fail."""
    root = Path(cryo.__file__).parent
    for path in root.rglob("*.py"):
        if "__tablename__" in path.read_text(encoding="utf-8"):
            module = ".".join(path.relative_to(root.parent).with_suffix("").parts)
            importlib.import_module(module)
    metadata = Base.metadata
    deleted = {"cryo.tables"}
    frontier = ["cryo.tables"]
    missing: list[str] = []
    while frontier:
        target = frontier.pop()
        for table in metadata.tables.values():
            for fk in table.foreign_keys:
                if fk.column.table.fullname != target:
                    continue
                action = (fk.ondelete or "").upper()
                if action not in {"CASCADE", "SET NULL"}:
                    missing.append(f"{table.fullname}.{fk.parent.name} -> {target}")
                if action == "CASCADE" and table.fullname not in deleted:
                    deleted.add(table.fullname)
                    frontier.append(table.fullname)
    assert missing == []


def test_purge_setting_is_off_by_default_and_bounded() -> None:
    assert Settings().removed_table_purge_after_days is None
    assert Settings(removed_table_purge_after_days=7).removed_table_purge_after_days == 7
    with pytest.raises(ValidationError):
        Settings(removed_table_purge_after_days=1)


def test_scheduler_purges_only_when_enabled_and_once_a_day(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from cryo.db.repositories import removed_table_purge
    from cryo.queue.redis_queue import InMemoryMaintenanceQueue
    from cryo.scheduler import service as scheduler_service
    from cryo.scheduler.service import InMemorySchedulerStore, SchedulerService

    calls: list[tuple[int, int]] = []

    def _purge(session: object, days: int, *, now: datetime, max_tables: int) -> object:
        calls.append((days, max_tables))
        return removed_table_purge.RemovedTablePurgeResult(0, 0, 0, 0, now)

    class _Session:
        def __enter__(self) -> _Session:
            return self

        def __exit__(self, *exc: object) -> None:
            return None

    monkeypatch.setattr(removed_table_purge, "purge_removed_tables", _purge)
    monkeypatch.setattr(scheduler_service, "SessionLocal", _Session)
    service = SchedulerService(InMemorySchedulerStore(), InMemoryMaintenanceQueue())

    service._maybe_purge_removed_tables(NOW)
    assert calls == []

    enabled = get_settings().model_copy(
        update={
            "removed_table_purge_after_days": 45,
            "removed_table_purge_max_tables_per_tick": 9,
        }
    )
    monkeypatch.setattr("cryo.config.get_settings", lambda: enabled)
    service._maybe_purge_removed_tables(NOW)
    service._maybe_purge_removed_tables(NOW + timedelta(hours=6))
    assert calls == [(45, 9)]
    service._maybe_purge_removed_tables(NOW + timedelta(days=1))
    assert calls == [(45, 9), (45, 9)]


def test_cli_refuses_to_run_when_purge_is_off(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from cryo import cli

    args = cli.create_parser().parse_args(["purge-removed-tables"])
    with pytest.raises(SystemExit) as exit_info:
        cli.run_purge_removed_tables(args)
    assert exit_info.value.code == 2
    assert "Purge is off" in capsys.readouterr().out


def test_cli_dry_run_lists_candidates_and_apply_purges(
    fk_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from cryo import cli

    fleet = _fleet(fk_session)
    gone_id = _table(fk_session, fleet, "gone", OLD).id
    fk_session.commit()

    class _Factory:
        def __call__(self) -> _Factory:
            return self

        def __enter__(self) -> Session:
            return fk_session

        def __exit__(self, *exc: object) -> None:
            return None

    monkeypatch.setattr("cryo.db.session.SessionLocal", _Factory())
    parser = cli.create_parser()

    with pytest.raises(SystemExit) as dry_exit:
        cli.run_purge_removed_tables(
            parser.parse_args(["purge-removed-tables", "--older-than-days", "7"])
        )
    out = capsys.readouterr().out
    assert dry_exit.value.code == 0
    assert "lake.sales.gone" in out and "Dry run" in out
    assert fk_session.get(Table, gone_id) is not None

    with pytest.raises(SystemExit) as apply_exit:
        cli.run_purge_removed_tables(
            parser.parse_args(["purge-removed-tables", "--older-than-days", "7", "--apply"])
        )
    assert apply_exit.value.code == 0
    assert "Purged 1 table(s)" in capsys.readouterr().out
    fk_session.expire_all()
    assert fk_session.get(Table, gone_id) is None
    (row,) = fk_session.scalars(
        select(AuditLog).where(AuditLog.action == TABLE_PURGED_ACTION)
    ).all()
    assert row.actor == "cli"
