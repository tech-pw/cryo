from __future__ import annotations

import itertools
import uuid
from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import Engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
from sqlalchemy.sql.elements import WithinGroup

from cryo.db.base import Base
from cryo.db.models import (
    AuditLog,
    AWSCostInsightConfig,
    AWSCurLineItem,
    AWSIntegration,
    AWSInventoryObject,
    DarkTableCandidate,
    Destination,
    LLMObservation,
    MaintenanceJob,
    MaintenanceRun,
    Preset,
    PresetRule,
    QueryCostAttributionDaily,
    QueryLog,
    QueryPattern,
    S3BucketLogConfig,
    S3RequestCostDaily,
    SharedS3AnomalyReport,
    SilentDataLossEvent,
    Source,
    Table,
    TableAlertDispatch,
    TableAlertSubscription,
    TableCostHistory,
    TablePartition,
    TableQueryActivity,
    TableStatsHistory,
)
from cryo.lineage.tracker import LineageEdge  # noqa: F401 — register table metadata
from cryo.security.abac import ABACPolicy  # noqa: F401 — register table metadata
from cryo.workflows.approvals import ApprovalRequest  # noqa: F401 — register table metadata
from tests.test_presets_api_db import api_db  # noqa: F401 — shared API+DB test client fixture

UUID_ID_MODELS = (
    Source,
    Destination,
    Preset,
    PresetRule,
    Table,
    MaintenanceRun,
    MaintenanceJob,
    AWSIntegration,
    AWSCostInsightConfig,
    AWSCurLineItem,
    AWSInventoryObject,
    LLMObservation,
    TableAlertDispatch,
    TableAlertSubscription,
    S3BucketLogConfig,
    S3RequestCostDaily,
    SilentDataLossEvent,
    DarkTableCandidate,
    SharedS3AnomalyReport,
)
_AUTOINCREMENT_ID_MODELS = (
    TablePartition,
    TableStatsHistory,
    TableCostHistory,
    TableQueryActivity,
    AuditLog,
    QueryLog,
    QueryPattern,
    QueryCostAttributionDaily,
)
_next_autoincrement_id = itertools.count(1)


@compiles(JSONB, "sqlite")
def _compile_jsonb_sqlite(type_: JSONB, compiler: object, **kwargs: object) -> str:
    return "JSON"


@event.listens_for(Engine, "connect")
def _register_sqlite_jsonb_array_length(
    dbapi_connection: object, connection_record: object
) -> None:
    """Postgres's jsonb_array_length() (used by get_table_query_patterns) has no sqlite
    equivalent registered by default -- sqlite's own json_array_length() does the same thing
    for the JSON-typed columns _compile_jsonb_sqlite maps JSONB onto, so just alias it. Only
    fires for sqlite connections (checked via a cheap attribute probe rather than importing
    the pysqlite driver here) so this is a no-op against the real Postgres test path."""
    if not hasattr(dbapi_connection, "create_function"):
        return
    import json as _json

    def _jsonb_array_length(value: object) -> int | None:
        if value is None:
            return None
        parsed = _json.loads(value) if isinstance(value, str) else value
        return len(parsed) if isinstance(parsed, list) else None

    dbapi_connection.create_function("jsonb_array_length", 1, _jsonb_array_length)


@event.listens_for(Engine, "connect")
def _register_sqlite_percentile_cont(
    dbapi_connection: object, connection_record: object
) -> None:
    """Postgres's ``percentile_cont(p) WITHIN GROUP (ORDER BY col)`` (used by
    compaction_candidates._table_p95_latency) has no sqlite equivalent -- sqlite doesn't support
    WITHIN GROUP syntax at all, so this can't be a same-shape function alias like
    jsonb_array_length above; instead we register a sqlite aggregate class that sqlite invokes
    per matching row and finishes with a linear-interpolation percentile, called via a plain
    ``percentile_cont(col, p)`` from a query rewrite (see _rewrite_percentile_cont_for_sqlite in
    _patch_sqlite_incompatible_defaults below). Only fires for sqlite connections; no-op against
    the real Postgres test path."""
    if not hasattr(dbapi_connection, "create_aggregate"):
        return

    class _PercentileCont:
        def __init__(self) -> None:
            self.values: list[float] = []
            self.percentile: float | None = None

        def step(self, value: float | None, percentile: float | None) -> None:
            if value is not None:
                self.values.append(float(value))
            if percentile is not None:
                self.percentile = float(percentile)

        def finalize(self) -> float | None:
            if not self.values or self.percentile is None:
                return None
            ordered = sorted(self.values)
            if len(ordered) == 1:
                return ordered[0]
            rank = self.percentile * (len(ordered) - 1)
            lo, hi = int(rank), min(int(rank) + 1, len(ordered) - 1)
            frac = rank - lo
            return ordered[lo] + (ordered[hi] - ordered[lo]) * frac

    dbapi_connection.create_aggregate("percentile_cont_agg", 2, _PercentileCont)


@compiles(WithinGroup, "sqlite")
def _compile_within_group_sqlite(element: WithinGroup, compiler: object, **kwargs: object) -> str:
    """Rewrite Postgres' ``percentile_cont(p) WITHIN GROUP (ORDER BY col)`` into a call to the
    ``percentile_cont_agg(col, p)`` sqlite aggregate registered above -- sqlite has no WITHIN
    GROUP syntax at all, so this can't stay a plain function-name alias."""
    func_name = element.element.name
    if func_name != "percentile_cont":
        # Only percentile_cont is special-cased; anything else falls back to sqlite's default
        # (likely still broken, but not something this codebase's tests currently exercise).
        return compiler.visit_withingroup(element, **kwargs)
    percentile_expr = compiler.process(list(element.element.clauses)[0], **kwargs)
    order_by_expr = compiler.process(element.order_by, **kwargs)
    return f"percentile_cont_agg({order_by_expr}, {percentile_expr})"


@pytest.fixture(autouse=True)
def _test_jwt_secret() -> None:
    from tests.auth_helpers import ensure_test_jwt_secret

    ensure_test_jwt_secret()


@pytest.fixture()
def session() -> Iterator[Session]:
    _patch_sqlite_incompatible_defaults()
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    with engine.begin() as connection:
        connection.execute(text("ATTACH DATABASE ':memory:' AS cryo"))
        Base.metadata.create_all(connection)

    session_factory = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )
    event.listen(session_factory, "before_flush", _assign_missing_uuid_primary_keys)
    event.listen(session_factory, "before_flush", _assign_missing_autoincrement_primary_keys)

    with session_factory() as db_session:
        yield db_session

    event.remove(session_factory, "before_flush", _assign_missing_uuid_primary_keys)
    event.remove(session_factory, "before_flush", _assign_missing_autoincrement_primary_keys)
    engine.dispose()


def _assign_missing_autoincrement_primary_keys(
    session: Session,
    flush_context: object,
    instances: object,
) -> None:
    for item in session.new:
        if not isinstance(item, _AUTOINCREMENT_ID_MODELS):
            continue
        mapper = inspect(item.__class__)
        pk_cols = mapper.primary_key
        if len(pk_cols) != 1:
            continue
        pk_attr = pk_cols[0].key
        if getattr(item, pk_attr) is None:
            setattr(item, pk_attr, next(_next_autoincrement_id))


def _assign_missing_uuid_primary_keys(
    session: Session,
    flush_context: object,
    instances: object,
) -> None:
    for item in session.new:
        if isinstance(item, UUID_ID_MODELS) and item.id is None:
            item.id = uuid.uuid4()


def _patch_sqlite_incompatible_defaults() -> None:
    for table in Base.metadata.tables.values():
        for column in table.columns:
            if column.server_default is None:
                continue
            default = str(column.server_default.arg)
            if "::jsonb" in default or "gen_random_uuid" in default:
                column.server_default = None
