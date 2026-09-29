"""Server-side predicates for Tables saved views.

These are not columns on ``tables`` or ``table_stats``. The clauses below use
the columns that actually exist:

- last commit: ``table_stats.last_snapshot_committed_at``
- freshness / maintenance / p95 SLA: pinned, enabled ``presets.guardrails``
  keys ``sla_freshness_hours``, ``sla_maintenance_days``, ``sla_p95_latency_ms``
- maintenance age: ``audit_log.ts`` where ``status`` is ``OK`` (what the
  coordinator writes) or ``succeeded``
- p95: ``query_logs.duration_ms`` over the trailing day, same window as
  ``GET /sla-status``
- reads: ``query_logs`` when that source has rows in the window, otherwise
  ``table_query_activity``

A missing guardrail key, assignment, commit, audit, or p95 is null. Null is
excluded from the match and is not a breach. ``DEFAULT_SLA`` is not applied.
Rule-matched presets are not a column; those tables are unknown here.

``unknown_count`` is the excluded null-side count from the same aggregate as
the match total, not a second table scan. Rows outside read-telemetry coverage
are not returned as unused. Dark-table confirmation windows and the 1 GB floor
stay on ``GET /dark-tables``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from typing import cast as type_cast

from sqlalchemy import (
    Integer,
    String,
    and_,
    case,
    cast,
    false,
    func,
    or_,
    select,
)
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import aliased
from sqlalchemy.sql.elements import ColumnElement
from sqlalchemy.sql.selectable import Select

from cryo.db.models import (
    AuditLog,
    Preset,
    QueryLog,
    Table,
    TableAssignment,
    TableQueryActivity,
    TableStats,
)

P95_WINDOW_DAYS = 1
SUCCESS_AUDIT_STATUSES = ("OK", "succeeded")
_GUARDRAIL_KEYS = frozenset(
    {
        "sla_freshness_hours",
        "sla_maintenance_days",
        "sla_p95_latency_ms",
    }
)


class JsonText(ColumnElement[str]):
    inherit_cache = False
    type = String()

    def __init__(self, column: ColumnElement[Any], key: str) -> None:
        if key not in _GUARDRAIL_KEYS:
            raise ValueError(f"unsupported guardrail key: {key}")
        self.column = column
        self.key = key


class HoursSince(ColumnElement[float]):
    inherit_cache = False

    def __init__(self, timestamp: ColumnElement[Any], now: datetime) -> None:
        self.timestamp = timestamp
        self.now = now.astimezone(UTC)


@compiles(JsonText, "sqlite")
def _json_text_sqlite(element: JsonText, compiler: Any, **kw: Any) -> str:
    column = compiler.process(element.column, **kw)
    return f"json_extract({column}, '$.{element.key}')"


@compiles(JsonText)
def _json_text_default(element: JsonText, compiler: Any, **kw: Any) -> str:
    column = compiler.process(element.column, **kw)
    return f"({column} ->> '{element.key}')"


@compiles(HoursSince, "sqlite")
def _hours_since_sqlite(element: HoursSince, compiler: Any, **kw: Any) -> str:
    stamp = element.now.strftime("%Y-%m-%d %H:%M:%S")
    timestamp = compiler.process(element.timestamp, **kw)
    return f"((julianday('{stamp}') - julianday({timestamp})) * 24.0)"


@compiles(HoursSince)
def _hours_since_default(element: HoursSince, compiler: Any, **kw: Any) -> str:
    stamp = element.now.strftime("%Y-%m-%d %H:%M:%S+00")
    timestamp = compiler.process(element.timestamp, **kw)
    return f"(EXTRACT(EPOCH FROM (TIMESTAMPTZ '{stamp}' - {timestamp})) / 3600.0)"


def _sum_flag(clause: ColumnElement[bool]) -> ColumnElement[Any]:
    return func.coalesce(func.sum(case((clause, 1), else_=0)), 0)


def _assigned_guardrail_int(key: str) -> ColumnElement[Any]:
    guardrails = type_cast("ColumnElement[Any]", Preset.guardrails)
    value = cast(JsonText(guardrails, key), Integer)
    return (
        select(value)
        .select_from(TableAssignment)
        .join(Preset, Preset.id == TableAssignment.preset_id)
        .where(
            TableAssignment.table_id == Table.id,
            TableAssignment.pinned.is_(True),
            Preset.enabled.is_(True),
        )
        .correlate(Table)
        .scalar_subquery()
    )


def _known_breach(
    actual: ColumnElement[Any],
    sla: ColumnElement[Any],
    overdue: ColumnElement[bool],
) -> tuple[ColumnElement[bool], ColumnElement[bool]]:
    known = and_(actual.is_not(None), sla.is_not(None))
    return and_(known, overdue), or_(actual.is_(None), sla.is_(None))


def freshness_clauses(now: datetime) -> tuple[ColumnElement[bool], ColumnElement[bool]]:
    sla = _assigned_guardrail_int("sla_freshness_hours")
    commit = type_cast("ColumnElement[Any]", TableStats.last_snapshot_committed_at)
    return _known_breach(commit, sla, HoursSince(commit, now) > sla)


def maintenance_clauses(now: datetime) -> tuple[ColumnElement[bool], ColumnElement[bool]]:
    sla = _assigned_guardrail_int("sla_maintenance_days")
    last_ts = (
        select(func.max(AuditLog.ts))
        .where(
            AuditLog.table_id == Table.id,
            AuditLog.status.in_(SUCCESS_AUDIT_STATUSES),
        )
        .correlate(Table)
        .scalar_subquery()
    )
    return _known_breach(last_ts, sla, HoursSince(last_ts, now) / 24.0 > sla)


def p95_clauses(now: datetime) -> tuple[ColumnElement[bool], ColumnElement[bool]]:
    sla = _assigned_guardrail_int("sla_p95_latency_ms")
    window_start = now - timedelta(days=P95_WINDOW_DAYS)
    p95 = (
        select(func.percentile_cont(0.95).within_group(QueryLog.duration_ms))
        .where(
            QueryLog.table_id == Table.id,
            QueryLog.executed_at >= window_start,
            QueryLog.executed_at < now,
            QueryLog.duration_ms.is_not(None),
        )
        .correlate(Table)
        .scalar_subquery()
    )
    return _known_breach(p95, sla, p95 > sla)


def no_reads_clauses(
    now: datetime, days: int
) -> tuple[ColumnElement[bool], ColumnElement[bool], ColumnElement[bool], ColumnElement[bool]]:
    window_start = now - timedelta(days=days)
    logs_source = (
        select(QueryLog.id)
        .where(
            QueryLog.source_id == Table.source_id,
            QueryLog.executed_at >= window_start,
            QueryLog.executed_at < now,
        )
        .correlate(Table)
        .exists()
    )
    table_reads = (
        select(QueryLog.id)
        .where(
            QueryLog.table_id == Table.id,
            QueryLog.executed_at >= window_start,
            QueryLog.executed_at < now,
        )
        .correlate(Table)
        .exists()
    )
    other = aliased(Table)
    activity_source = (
        select(TableQueryActivity.id)
        .join(other, other.id == TableQueryActivity.table_id)
        .where(
            other.source_id == Table.source_id,
            TableQueryActivity.day >= window_start.date(),
            TableQueryActivity.day <= now.date(),
            TableQueryActivity.query_count > 0,
        )
        .correlate(Table)
        .exists()
    )
    table_activity = (
        select(func.coalesce(func.sum(TableQueryActivity.query_count), 0))
        .where(
            TableQueryActivity.table_id == Table.id,
            TableQueryActivity.day >= window_start.date(),
            TableQueryActivity.day <= now.date(),
        )
        .correlate(Table)
        .scalar_subquery()
    )
    logs_covered = logs_source
    activity_covered = and_(~logs_source, activity_source)
    gap = and_(~logs_source, ~activity_source)
    match = or_(
        and_(logs_covered, ~table_reads),
        and_(activity_covered, table_activity == 0),
    )
    return match, logs_covered, activity_covered, gap


@dataclass(frozen=True)
class TableViewFilter:
    clause: ColumnElement[bool] | None
    unknown_clause: ColumnElement[bool] | None
    logs_covered_clause: ColumnElement[bool] | None
    activity_covered_clause: ColumnElement[bool] | None
    gap_clause: ColumnElement[bool] | None
    tracks_unknown: bool
    window_days: int | None
    cursor_token: str

    def cursor_filter(self, filter_param: str | None) -> str | None:
        if not self.cursor_token:
            return filter_param
        if filter_param:
            return f"{filter_param}|{self.cursor_token}"
        return self.cursor_token

    def metrics_query(self, count_stmt: Select[Any]) -> Select[Any]:
        if self.clause is None:
            raise ValueError("metrics_query requires an active view filter")
        unknown = self.unknown_clause if self.unknown_clause is not None else false()
        logs = self.logs_covered_clause if self.logs_covered_clause is not None else false()
        activity = (
            self.activity_covered_clause if self.activity_covered_clause is not None else false()
        )
        gap = self.gap_clause if self.gap_clause is not None else false()
        return count_stmt.with_only_columns(
            _sum_flag(self.clause).label("matched"),
            _sum_flag(unknown).label("unknown"),
            _sum_flag(logs).label("logs_covered"),
            _sum_flag(activity).label("activity_covered"),
            _sum_flag(gap).label("gap"),
            maintain_column_froms=True,
        )

    def response_fields(self, row: Any | None) -> dict[str, Any]:
        if row is None:
            return {
                "unknown_count": None,
                "telemetry_window_days": None,
                "telemetry_source": None,
                "telemetry_coverage": None,
                "telemetry_gap_tables": None,
            }
        unknown_count = int(row.unknown or 0) if self.tracks_unknown else None
        if self.window_days is None:
            return {
                "unknown_count": unknown_count,
                "telemetry_window_days": None,
                "telemetry_source": None,
                "telemetry_coverage": None,
                "telemetry_gap_tables": None,
            }
        logs_covered = int(row.logs_covered or 0)
        activity_covered = int(row.activity_covered or 0)
        gap = int(row.gap or 0)
        covered = logs_covered + activity_covered
        denom = covered + gap
        if logs_covered and activity_covered:
            source = "mixed"
        elif logs_covered:
            source = "query_logs"
        elif activity_covered:
            source = "table_query_activity"
        else:
            source = "none"
        return {
            "unknown_count": unknown_count,
            "telemetry_window_days": self.window_days,
            "telemetry_source": source,
            "telemetry_coverage": (covered / denom) if denom else None,
            "telemetry_gap_tables": gap,
        }


def build_table_view_filter(
    *,
    freshness_breach: bool = False,
    maintenance_overdue: bool = False,
    p95_breach: bool = False,
    no_reads_days: int | None = None,
    now: datetime | None = None,
) -> TableViewFilter:
    current = now or datetime.now(UTC)
    matches: list[ColumnElement[bool]] = []
    unknowns: list[ColumnElement[bool]] = []
    logs_covered: ColumnElement[bool] | None = None
    activity_covered: ColumnElement[bool] | None = None
    gap: ColumnElement[bool] | None = None
    token: list[str] = []
    if freshness_breach:
        match, unknown = freshness_clauses(current)
        matches.append(match)
        unknowns.append(unknown)
        token.append("freshness_breach=1")
    if maintenance_overdue:
        match, unknown = maintenance_clauses(current)
        matches.append(match)
        unknowns.append(unknown)
        token.append("maintenance_overdue=1")
    if p95_breach:
        match, unknown = p95_clauses(current)
        matches.append(match)
        unknowns.append(unknown)
        token.append("p95_breach=1")
    if no_reads_days is not None:
        match, logs_covered, activity_covered, gap = no_reads_clauses(current, no_reads_days)
        matches.append(match)
        token.append(f"no_reads_days={no_reads_days}")
    clause = and_(*matches) if matches else None
    unknown_clause = or_(*unknowns) if unknowns else None
    return TableViewFilter(
        clause=clause,
        unknown_clause=unknown_clause,
        logs_covered_clause=logs_covered,
        activity_covered_clause=activity_covered,
        gap_clause=gap,
        tracks_unknown=bool(unknowns),
        window_days=no_reads_days,
        cursor_token="&".join(token),
    )
