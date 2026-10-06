"""Declarative quality constraints over stored scan metrics (Deequ-style, ZSDQ §5.4)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from cryo.api.routes_quality_constraints import (
    QualityConstraintCreate,
    QualityConstraintUpdate,
    create_quality_constraint,
    delete_quality_constraint,
    evaluate_quality_constraints,
    list_constraint_results,
    list_quality_constraints,
    update_quality_constraint,
)
from cryo.api.security import Principal, Role
from cryo.db.models import AuditLog, QualityConstraintResult, Source, Table, TableStats
from cryo.quality.constraint_store import (
    RESULT_RETENTION,
    evaluate_after_scan,
    evaluate_table_constraints,
)
from cryo.quality.constraints import (
    ConstraintKind,
    ConstraintSpec,
    MetricsView,
    column_kinds,
    evaluate,
    validate_spec,
)
from cryo.scheduler.alert_dispatcher import _constraint_breach_reasons

PRINCIPAL = Principal(subject="test-operator", roles=frozenset({Role.OPERATOR}))
SCANNED = datetime(2026, 9, 24, 6, tzinfo=UTC)


def _col(field_id: int, name: str, column_type: str, **kw: Any) -> dict[str, Any]:
    entry = {
        "field_id": field_id,
        "column_name": name,
        "column_type": column_type,
        "null_count": 0,
        "value_count": 1000,
        "lower_bound": None,
        "upper_bound": None,
        "bounds_complete": None,
    }
    entry.update(kw)
    return entry


def _view(
    *,
    records: int | None = 1000,
    live: int | None = None,
    pos: int = 0,
    eq: int = 0,
    columns: list[dict[str, Any]] | None = None,
    hours: float | None = None,
    data_commits: int | None = None,
    snapshot_id: int | None = 7,
) -> MetricsView:
    return MetricsView(
        snapshot_id=snapshot_id,
        scanned_at=SCANNED,
        data_records=records,
        live_rows=live,
        position_delete_files=pos,
        equality_delete_files=eq,
        columns={c["field_id"]: c for c in columns or []},
        hours_since_data_commit=hours,
        data_commits=data_commits,
    )


def _spec(kind: ConstraintKind, field_id: int | None = None, **params: Any) -> ConstraintSpec:
    return ConstraintSpec(kind=kind, params=params, field_id=field_id)


class TestSize:
    def test_exact_without_deletes(self) -> None:
        assert evaluate(_spec(ConstraintKind.SIZE, min_rows=10), _view()).status == "passed"
        failed = evaluate(_spec(ConstraintKind.SIZE, max_rows=999), _view())
        assert failed.status == "failed"
        assert failed.observed == {"live_rows": 1000}

    def test_position_deletes_use_merge_on_read_live_rows(self) -> None:
        result = evaluate(_spec(ConstraintKind.SIZE, max_rows=900), _view(live=900, pos=3))
        assert result.status == "passed"
        assert result.observed == {"live_rows": 900}

    def test_equality_deletes_are_unknown(self) -> None:
        result = evaluate(_spec(ConstraintKind.SIZE, min_rows=1), _view(live=900, eq=1))
        assert result.status == "unknown"
        assert "equality deletes" in (result.reason or "")


class TestCompleteness:
    def test_exact_pass_and_fail(self) -> None:
        cols = [_col(1, "user_id", "long", null_count=5)]
        spec = _spec(ConstraintKind.COMPLETENESS, 1, min=0.99)
        assert evaluate(spec, _view(columns=cols)).status == "passed"
        failed = evaluate(_spec(ConstraintKind.COMPLETENESS, 1, min=1.0), _view(columns=cols))
        assert failed.status == "failed"
        assert failed.observed["completeness"] == pytest.approx(0.995)

    def test_files_without_metrics_make_it_unknown(self) -> None:
        cols = [_col(1, "user_id", "long", value_count=800)]
        result = evaluate(_spec(ConstraintKind.COMPLETENESS, 1, min=0.5), _view(columns=cols))
        assert result.status == "unknown"
        assert result.evidence["coverage"] == 0.8

    def test_deletes_pass_on_worst_case_but_never_fail(self) -> None:
        cols = [_col(1, "user_id", "long", null_count=10)]
        view = _view(columns=cols, live=500, pos=2)
        assert evaluate(_spec(ConstraintKind.COMPLETENESS, 1, min=0.98), view).status == "passed"
        result = evaluate(_spec(ConstraintKind.COMPLETENESS, 1, min=0.999), view)
        assert result.status == "unknown"
        assert result.observed["completeness_lower_bound"] == pytest.approx(0.98)

    def test_dropped_column_is_unknown(self) -> None:
        result = evaluate(_spec(ConstraintKind.COMPLETENESS, 9, min=1.0), _view(columns=[]))
        assert result.status == "unknown"


class TestRange:
    def _amount(self, **kw: Any) -> list[dict[str, Any]]:
        base = {"lower_bound": 0, "upper_bound": 250.5, "bounds_complete": True}
        base.update(kw)
        return [_col(2, "amount", "decimal(12, 2)", **base)]

    def test_pass_and_fail_without_deletes(self) -> None:
        view = _view(columns=self._amount())
        assert evaluate(_spec(ConstraintKind.RANGE, 2, min=0), view).status == "passed"
        failed = evaluate(_spec(ConstraintKind.RANGE, 2, max="100"), view)
        assert failed.status == "failed"
        assert failed.observed == {"min": 0, "max": 250.5}

    def test_violation_under_deletes_is_unknown(self) -> None:
        view = _view(columns=self._amount(), live=990, pos=1)
        assert evaluate(_spec(ConstraintKind.RANGE, 2, max=100), view).status == "unknown"
        assert evaluate(_spec(ConstraintKind.RANGE, 2, max=300), view).status == "passed"

    @pytest.mark.parametrize("complete", [False, None])
    def test_partial_or_untracked_bounds_are_unknown(self, complete: bool | None) -> None:
        view = _view(columns=self._amount(bounds_complete=complete))
        assert evaluate(_spec(ConstraintKind.RANGE, 2, min=0), view).status == "unknown"

    def test_all_null_column_passes(self) -> None:
        cols = self._amount(null_count=1000, lower_bound=None, upper_bound=None)
        assert evaluate(_spec(ConstraintKind.RANGE, 2, min=0), _view(columns=cols)).status == (
            "passed"
        )

    def test_temporal_bounds_from_both_scanners(self) -> None:
        cols = [
            _col(
                3,
                "d",
                "date",
                lower_bound="2026-01-01",
                upper_bound="2026-09-23",
                bounds_complete=True,
            ),
            _col(
                4,
                "ts",
                "timestamptz",
                lower_bound="2026-01-01T00:00:00.123456789Z",
                upper_bound="2026-09-23T10:00:00+00:00",
                bounds_complete=True,
            ),
        ]
        view = _view(columns=cols)
        assert evaluate(_spec(ConstraintKind.RANGE, 3, max="2026-09-24"), view).status == "passed"
        assert evaluate(_spec(ConstraintKind.RANGE, 4, min="2026-06-01T00:00:00"), view).status == (
            "failed"
        )


class TestFreshnessAndChange:
    def test_freshness_is_as_of_the_scan(self) -> None:
        assert evaluate(_spec(ConstraintKind.FRESHNESS, max_hours=24), _view(hours=3)).status == (
            "passed"
        )
        assert evaluate(_spec(ConstraintKind.FRESHNESS, max_hours=24), _view(hours=30)).status == (
            "failed"
        )
        unknown = evaluate(_spec(ConstraintKind.FRESHNESS, max_hours=24), _view(data_commits=0))
        assert unknown.status == "unknown"

    def test_size_change_needs_a_baseline(self) -> None:
        spec = _spec(ConstraintKind.SIZE_CHANGE, max_decrease=0.2, max_increase=1.0)
        first = evaluate(spec, _view())
        assert first.status == "unknown"
        assert first.observed == {"live_rows": 1000}
        assert evaluate(spec, _view(), baseline={"live_rows": 1100}).status == "passed"
        dropped = evaluate(spec, _view(), baseline={"live_rows": 2000})
        assert dropped.status == "failed"
        assert dropped.observed["relative_change"] == -0.5


class TestValidation:
    @pytest.mark.parametrize(
        ("spec", "column"),
        [
            (_spec(ConstraintKind.SIZE), None),
            (_spec(ConstraintKind.SIZE, min_rows=5, max_rows=1), None),
            (_spec(ConstraintKind.COMPLETENESS, 1, min=0), None),
            (_spec(ConstraintKind.COMPLETENESS, min=0.5), None),
            (_spec(ConstraintKind.RANGE, 1, min=0), _col(1, "email", "string")),
            (_spec(ConstraintKind.RANGE, 1, min="x"), _col(1, "n", "long")),
            (_spec(ConstraintKind.RANGE, 1, min=5, max=1), _col(1, "n", "long")),
            (_spec(ConstraintKind.COMPLETENESS, 1, min=1), _col(1, "tags", "list<string>")),
            (_spec(ConstraintKind.SIZE_CHANGE, max_decrease=2), None),
            (_spec(ConstraintKind.FRESHNESS, max_hours=0), None),
            (_spec(ConstraintKind.FRESHNESS, 1, max_hours=1), None),
        ],
    )
    def test_rejects(self, spec: ConstraintSpec, column: dict[str, Any] | None) -> None:
        with pytest.raises(ValueError):
            validate_spec(spec, column)

    def test_column_kinds(self) -> None:
        assert column_kinds(_col(1, "n", "long")) == ["completeness", "range"]
        assert column_kinds(_col(1, "s", "string")) == ["completeness"]
        assert column_kinds(_col(1, "m", "map<string, long>")) == []


def _table(session: Session, *, raw: dict[str, Any], total_records: int = 1000) -> Table:
    source = Source(id=uuid.uuid4(), name=f"src-{uuid.uuid4()}", type="hive", config={})
    session.add(source)
    session.flush()
    table = Table(id=uuid.uuid4(), source_id=source.id, schema_name="s", table_name="orders")
    session.add(table)
    session.flush()
    session.add(
        TableStats(
            table_id=table.id,
            scan_at=SCANNED,
            needs_compaction=False,
            compaction_priority="LOW",
            grade="A",
            overall_score=95,
            total_files=4,
            small_files_count=0,
            total_size_gb=1.0,
            total_records=total_records,
            position_delete_files=0,
            equality_delete_files=0,
            raw=raw,
        )
    )
    session.commit()
    return table


def _raw(snapshot_id: int = 1, *, amount_max: float = 50.0) -> dict[str, Any]:
    return {
        "current_snapshot_id": snapshot_id,
        "column_quality": [
            _col(1, "user_id", "long", null_count=0),
            _col(
                2, "amount", "double", lower_bound=0.0, upper_bound=amount_max, bounds_complete=True
            ),
            _col(3, "tags", "list<string>", value_count=3000),
        ],
        "commit_signals": {"live_rows": 1000, "hours_since_data_commit": 2.0, "data_commits": 9},
    }


def _create(session: Session, table: Table, **kw: Any) -> Any:
    return create_quality_constraint(table.id, QualityConstraintCreate(**kw), session, PRINCIPAL)


class TestStoreAndRoutes:
    def test_create_evaluates_immediately_and_audits(self, session: Session) -> None:
        table = _table(session, raw=_raw())
        created = _create(
            session, table, name="amount capped", kind="range", field_id=2, params={"max": 100}
        )
        assert created.column_name == "amount"
        assert created.latest is not None and created.latest.status == "passed"
        actions = session.scalars(select(AuditLog.action).where(AuditLog.table_id == table.id))
        assert list(actions) == ["create_quality_constraint"]

        listed = list_quality_constraints(table.id, session)
        assert [c.name for c in listed.constraints] == ["amount capped"]
        assert listed.constraints[0].latest is not None
        assert {c.name: c.kinds for c in listed.columns} == {
            "user_id": ["completeness", "range"],
            "amount": ["completeness", "range"],
        }

    def test_invalid_and_duplicate_constraints_are_rejected(self, session: Session) -> None:
        table = _table(session, raw=_raw())
        with pytest.raises(HTTPException) as exc:
            _create(session, table, name="x", kind="range", field_id=99, params={"max": 1})
        assert exc.value.status_code == 422
        with pytest.raises(HTTPException) as exc:
            _create(session, table, name="x", kind="completeness", field_id=1, params={"min": 2})
        assert exc.value.status_code == 422
        _create(session, table, name="dup", kind="size", params={"min_rows": 1})
        with pytest.raises(HTTPException) as exc:
            _create(session, table, name="dup", kind="size", params={"min_rows": 2})
        assert exc.value.status_code == 409

    def test_failed_error_constraint_raises_an_alert_reason(self, session: Session) -> None:
        table = _table(session, raw=_raw(amount_max=500.0))
        _create(session, table, name="cap", kind="range", field_id=2, params={"max": 100})
        _create(
            session,
            table,
            name="soft cap",
            kind="range",
            field_id=2,
            params={"max": 100},
            severity="warn",
        )
        reasons = _constraint_breach_reasons(session, table.id)
        assert len(reasons) == 1
        assert "'cap' failed" in reasons[0]

    def test_unchanged_results_are_not_duplicated_and_history_is_bounded(
        self, session: Session
    ) -> None:
        table = _table(session, raw=_raw())
        created = _create(session, table, name="rows", kind="size", params={"min_rows": 1})
        for _ in range(3):
            evaluate_quality_constraints(table.id, session, PRINCIPAL)
        assert len(list_constraint_results(created.id, session, limit=100).results) == 1

        stats = session.get(TableStats, table.id)
        assert stats is not None
        for snapshot in range(2, RESULT_RETENTION + 10):
            stats.raw = _raw(snapshot)
            evaluate_table_constraints(session, table.id)
        session.commit()
        count = session.query(QualityConstraintResult).filter_by(constraint_id=created.id).count()
        assert count == RESULT_RETENTION

    def test_size_change_baselines_on_an_earlier_snapshot(self, session: Session) -> None:
        table = _table(session, raw=_raw(1))
        created = _create(
            session, table, name="no big drop", kind="size_change", params={"max_decrease": 0.3}
        )
        assert created.latest is not None and created.latest.status == "unknown"

        stats = session.get(TableStats, table.id)
        assert stats is not None
        stats.raw = {**_raw(2), "commit_signals": {"live_rows": 400}}
        stats.total_records = 400
        session.commit()
        evaluated = evaluate_quality_constraints(table.id, session, PRINCIPAL)
        latest = evaluated.constraints[0].latest
        assert latest is not None and latest.status == "failed"
        assert latest.observed["baseline_live_rows"] == 1000
        assert latest.evidence["baseline_snapshot_id"] == 1

    def test_update_revalidates_and_delete_cascades(self, session: Session) -> None:
        table = _table(session, raw=_raw())
        created = _create(
            session, table, name="complete", kind="completeness", field_id=1, params={"min": 1.0}
        )
        with pytest.raises(HTTPException):
            update_quality_constraint(
                created.id, QualityConstraintUpdate(params={"min": 5}), session, PRINCIPAL
            )
        session.rollback()
        updated = update_quality_constraint(
            created.id, QualityConstraintUpdate(severity="warn"), session, PRINCIPAL
        )
        assert updated.severity == "warn"
        delete_quality_constraint(created.id, session, PRINCIPAL)
        assert session.query(QualityConstraintResult).count() == 0

    def test_after_scan_hook_swallows_failures(
        self, session: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        table = _table(session, raw=_raw())
        _create(session, table, name="rows", kind="size", params={"min_rows": 1})

        def boom(*_: Any, **__: Any) -> Any:
            raise RuntimeError("bad stats")

        monkeypatch.setattr("cryo.quality.constraint_store.evaluate", boom)
        evaluate_after_scan(session, table.id)
        session.commit()


def test_python_scanner_tracks_bound_completeness() -> None:
    from cryo.scanner.quality import ColumnQualityAccumulator

    class Field:
        def __init__(self, name: str, field_type: str) -> None:
            self.name = name
            self.field_type = field_type

    class Schema:
        fields = {1: Field("amount", "long"), 2: Field("late", "long"), 3: Field("email", "string")}

        def find_field(self, field_id: int) -> Field:
            return self.fields[field_id]

    class DataFile:
        def __init__(self, **kw: Any) -> None:
            self.__dict__.update(kw)

    acc = ColumnQualityAccumulator(Schema())
    acc.add(
        DataFile(
            null_value_counts={1: 0, 2: 0},
            value_counts={1: 5, 2: 5, 3: 5},
            lower_bounds={1: 3, 2: 1},
            upper_bounds={1: 9, 2: 2},
        )
    )
    acc.add(
        DataFile(
            null_value_counts={2: 1},
            value_counts={1: 5, 2: 5, 3: 5},
            lower_bounds={1: -2},
            upper_bounds={1: 4},
        )
    )
    acc.add(
        DataFile(null_value_counts={1: 5}, value_counts={1: 5}, lower_bounds={}, upper_bounds={})
    )
    stats = {s.column_name: s for s in acc.finish()}
    assert (stats["amount"].min_value, stats["amount"].max_value) == (-2, 9)
    assert stats["amount"].bounds_complete is True
    assert stats["late"].bounds_complete is False
    assert stats["email"].bounds_complete is None
